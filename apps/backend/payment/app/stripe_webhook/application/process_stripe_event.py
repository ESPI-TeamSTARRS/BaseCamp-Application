import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.confirmation.application import confirmation_event_names as confirmation_names
from app.core.events.business_event import BusinessEvent, Scalar
from app.core.events.business_event_logger import BusinessEventLogger
from app.intent.domain.entities.payment_intent import PaymentIntent
from app.intent.domain.enums.intent_status import IntentStatus
from app.ledger.domain.entities.ledger_transaction import LedgerTransaction
from app.refund.domain.exceptions.stripe_refund_unavailable_error import (
    StripeRefundUnavailableError,
)
from app.refund.domain.interfaces.stripe_refund_gateway import StripeRefundGateway
from app.refund.domain.value_objects.stripe_refund_request import StripeRefundRequest
from app.stripe_webhook.application import stripe_webhook_event_names as names
from app.stripe_webhook.domain.entities.processed_stripe_event import ProcessedStripeEvent
from app.stripe_webhook.domain.errors.duplicate_stripe_event_error import (
    DuplicateStripeEventError,
)
from app.stripe_webhook.domain.interfaces.stripe_event_session import StripeEventSession
from app.stripe_webhook.domain.interfaces.stripe_event_unit_of_work import StripeEventUnitOfWork
from app.stripe_webhook.domain.value_objects.stripe_event import StripeEvent
from app.webhook.application import webhook_event_names as webhook_names
from app.webhook.domain.entities.outbox_event import OutboxEvent
from app.webhook.domain.services.payment_event_factory import PaymentEventFactory

CHECKOUT_COMPLETED = "checkout.session.completed"
PAYMENT_FAILED = "payment_intent.payment_failed"
REFUND_TYPES = frozenset({"refund.created", "refund.updated", "refund.failed"})
LATE_PAYMENT_ORIGIN = "late_payment"
_STRIPE_PAYMENT_REFERENCE = re.compile(r"pi_[A-Za-z0-9_]{1,200}")
# Intentions mortes : un paiement qui arrive après coup n'a plus de commande à confirmer.
_DEAD_STATUSES = frozenset({IntentStatus.FAILED, IntentStatus.CANCELED, IntentStatus.EXPIRED})
# Écarts qui laissent un paiement encaissé sans commande confirmée : une alerte (erreur), pas un simple refus.
_ALERT_REASONS = frozenset(
    {"amount_mismatch", "currency_mismatch", "session_mismatch", "intent_not_pending"}
)


@dataclass(frozen=True)
class _Outcome:
    """Ce qu'un événement a produit : réglé (état + grand livre + événement boutique) ou ignoré."""

    name: str
    reason: str | None = None
    intent: PaymentIntent | None = None
    ledger: LedgerTransaction | None = None
    outbox_event: OutboxEvent | None = None
    payment_reference: str | None = None


class ProcessStripeEventUseCase:
    """Traite un événement Stripe authentifié : seul le webhook signé fait foi du paiement.

    - `checkout.session.completed` payé : si la session, le montant et la devise correspondent à
      l'intention, elle passe `succeeded`, le grand livre est écrit en double entrée et l'événement
      pour la boutique est ajouté à l'outbox, dans UNE transaction, intention verrouillée.
    - `payment_intent.payment_failed` : intention `failed`, aucun grand livre.
    - Tout écart (montant, devise, session, intention déjà réglée) est ignoré avec une alerte : rien
      n'est confirmé. Les autres types sont ignorés.
    - Idempotence : l'identifiant d'événement est enregistré avec l'état ; un rejeu n'a aucun effet.
    Les événements de résultat ne sont journalisés qu'après la validation de la transaction, sans
    jamais le corps reçu.
    """

    def __init__(
        self,
        unit_of_work: StripeEventUnitOfWork,
        clock: Callable[[], datetime],
        events: BusinessEventLogger,
        refund_gateway: StripeRefundGateway | None = None,
        event_factory: PaymentEventFactory | None = None,
    ) -> None:
        self._refund_gateway = refund_gateway
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._events = events
        self._event_factory = event_factory or PaymentEventFactory()

    def execute(self, event: StripeEvent) -> None:
        if event.type not in {CHECKOUT_COMPLETED, PAYMENT_FAILED} | REFUND_TYPES:
            self._ignored(event, "unsupported_type", None)
            return
        intent_id = self._intent_reference(event.object)
        if intent_id is None:
            self._ignored(event, "intent_reference_missing", None)
            return
        now = self._clock()
        try:
            with self._unit_of_work.begin() as session:
                if session.is_processed(event.id):
                    self._replayed(event, intent_id)
                    return
                outcome = self._decide(session, event, intent_id, now)
                if outcome.intent and outcome.outbox_event:
                    session.save_settlement(outcome.intent, outcome.ledger, outcome.outbox_event)
                    if outcome.payment_reference:
                        session.save_payment_reference(intent_id, outcome.payment_reference)
                session.record_event(
                    ProcessedStripeEvent(event.id, event.type, outcome.name, intent_id, now)
                )
        except DuplicateStripeEventError:
            self._replayed(event, intent_id)
            return
        self._log(event, intent_id, outcome)

    def _decide(
        self, session: StripeEventSession, event: StripeEvent, intent_id: UUID, now: datetime
    ) -> _Outcome:
        intent = session.lock_intent(intent_id)
        if intent is None:
            return _Outcome("ignored", "intent_not_found")
        if event.type == PAYMENT_FAILED:
            return self._fail(intent, now)
        if event.type in REFUND_TYPES:
            return self._reconcile_refund(session, event, intent)
        refusal = self._completion_refusal(session, event, intent)
        if refusal:
            return _Outcome("ignored", refusal, intent)
        reference = self._payment_reference(event.object)
        if intent.status in _DEAD_STATUSES:
            return self._refund_late_payment(intent, reference)
        if intent.status is not IntentStatus.REQUIRES_CONFIRMATION:
            return _Outcome("ignored", "intent_not_pending", intent)
        settled = intent.succeed()
        ledger = LedgerTransaction.payment_received(intent.id, intent.amount, now)
        return _Outcome(
            "confirmed",
            None,
            settled,
            ledger,
            self._event_factory.build(settled, now),
            reference,
        )

    def _fail(self, intent: PaymentIntent, now: datetime) -> _Outcome:
        if intent.status is not IntentStatus.REQUIRES_CONFIRMATION:
            return _Outcome("ignored", "intent_not_pending", intent)
        failed = intent.fail()
        return _Outcome("failed", None, failed, None, self._event_factory.build(failed, now))

    def _refund_late_payment(self, intent: PaymentIntent, reference: str | None) -> _Outcome:
        """Une carte valide a payé une session dont l'intention est déjà morte (ex. première carte
        refusée puis seconde acceptée) : l'argent est pris pour une commande qui n'existe plus, il est
        rendu tout de suite, en entier, chez Stripe. Rien n'est écrit au grand livre : ni encaissement
        ni remboursement n'y ont été enregistrés. La clé d'idempotence est propre à l'intention, quel
        que soit l'événement qui le déclenche. Si Stripe échoue, la transaction est annulée et la route
        répond 503 : Stripe renvoie l'événement plus tard."""
        if self._refund_gateway is None or reference is None:
            return _Outcome("ignored", "intent_not_pending", intent)
        try:
            created = self._refund_gateway.create_refund(
                StripeRefundRequest(
                    intent.id,
                    reference,
                    intent.amount.cents,
                    LATE_PAYMENT_ORIGIN,
                    f"basecamp-late-refund-{intent.id}",
                )
            )
            if not created.accepted:
                raise StripeRefundUnavailableError("stripe_refund_not_accepted")
        except StripeRefundUnavailableError as error:
            self._events.log(
                BusinessEvent.error(
                    names.STRIPE_LATE_REFUND_FAILED,
                    error.reason,
                    **self._fields(str(intent.id), self._attributes(intent)),
                )
            )
            raise
        return _Outcome("refunded_late", None, intent)

    @staticmethod
    def _reconcile_refund(
        session: StripeEventSession, event: StripeEvent, intent: PaymentIntent
    ) -> _Outcome:
        """Rapproche l'événement d'un remboursement de celui que nous avons émis : jamais d'écriture au
        grand livre ici (déjà faite à l'émission), seulement une confirmation ou une alerte."""
        stripe_id = event.object.get("id")
        metadata = event.object.get("metadata")
        origin = metadata.get("basecamp_origin") if isinstance(metadata, dict) else None
        known = isinstance(stripe_id, str) and session.has_stripe_refund(stripe_id)
        if not known and origin != LATE_PAYMENT_ORIGIN:
            return _Outcome("ignored", "refund_unknown", intent)
        if event.type == "refund.failed" or event.object.get("status") in {"failed", "canceled"}:
            return _Outcome("refund_failed", "refund_failed_at_stripe", intent)
        return _Outcome("refund_confirmed", None, intent)

    @staticmethod
    def _completion_refusal(
        session: StripeEventSession, event: StripeEvent, intent: PaymentIntent
    ) -> str | None:
        """Motif de refus d'une session terminée : paiement, session, montant, devise (dans cet ordre)."""
        if event.object.get("payment_status") != "paid":
            return "payment_not_paid"
        if event.object.get("id") != session.get_checkout_session_id(intent.id):
            return "session_mismatch"
        amount = event.object.get("amount_total")
        if isinstance(amount, bool) or amount != intent.amount.cents:
            return "amount_mismatch"
        currency = event.object.get("currency")
        if not isinstance(currency, str) or currency.upper() != intent.currency.value:
            return "currency_mismatch"
        return None

    @staticmethod
    def _payment_reference(event_object: Mapping[str, Any]) -> str | None:
        value = event_object.get("payment_intent")
        if isinstance(value, str) and _STRIPE_PAYMENT_REFERENCE.fullmatch(value):
            return value
        return None

    @staticmethod
    def _intent_reference(event_object: Mapping[str, Any]) -> UUID | None:
        metadata = event_object.get("metadata")
        value = metadata.get("payment_intent_id") if isinstance(metadata, dict) else None
        try:
            return UUID(value) if isinstance(value, str) else None
        except ValueError:
            return None

    def _log(self, event: StripeEvent, intent_id: UUID, outcome: _Outcome) -> None:
        if outcome.name == "ignored":
            self._ignored(event, outcome.reason or "ignored", outcome.intent, intent_id)
            return
        if outcome.name in {"refund_confirmed", "refund_failed", "refunded_late"}:
            self._log_refund(event, intent_id, outcome)
            return
        intent = outcome.intent
        if (
            intent is None or outcome.outbox_event is None
        ):  # impossible : réglé = intention + événement
            return
        fields = self._fields(str(intent_id), self._attributes(intent))
        if outcome.name == "confirmed":
            self._events.log(BusinessEvent.success(confirmation_names.PAYMENT_CONFIRMED, **fields))
        else:
            self._events.log(
                BusinessEvent.denied(
                    confirmation_names.PAYMENT_DECLINED, "provider_declined", **fields
                )
            )
        if outcome.ledger:
            written: dict[str, Scalar] = {
                "transaction_id": str(outcome.ledger.id),
                "entry_count": len(outcome.ledger.entries),
                "amount_cents": intent.amount.cents,
            }
            self._events.log(
                BusinessEvent.success(
                    confirmation_names.LEDGER_ENTRIES_WRITTEN,
                    **self._fields(str(intent_id), written),
                )
            )
        self._events.log(
            BusinessEvent.success(
                webhook_names.WEBHOOK_EVENT_QUEUED,
                resource_type="webhook_event",
                resource_id=str(outcome.outbox_event.id),
                attributes={
                    "event_type": outcome.outbox_event.event_type.value,
                    "payment_intent_id": str(intent_id),
                },
            )
        )
        self._events.log(
            BusinessEvent.success(
                names.STRIPE_EVENT_PROCESSED,
                **self._fields(
                    str(intent_id),
                    {"stripe_event_type": event.type, "outcome": outcome.name},
                ),
            )
        )

    def _log_refund(self, event: StripeEvent, intent_id: UUID, outcome: _Outcome) -> None:
        intent = outcome.intent
        attributes: dict[str, Scalar] = {"stripe_event_type": event.type, "outcome": outcome.name}
        if intent:
            attributes.update(self._attributes(intent))
        fields = self._fields(str(intent_id), attributes)
        if outcome.name == "refund_failed":
            self._events.log(
                BusinessEvent.error(
                    names.STRIPE_REFUND_FAILED, outcome.reason or "failed", **fields
                )
            )
        elif outcome.name == "refunded_late":
            self._events.log(BusinessEvent.success(names.STRIPE_LATE_PAYMENT_REFUNDED, **fields))
        else:
            self._events.log(BusinessEvent.success(names.STRIPE_REFUND_CONFIRMED, **fields))

    def _ignored(
        self,
        event: StripeEvent,
        reason: str,
        intent: PaymentIntent | None,
        intent_id: UUID | None = None,
    ) -> None:
        attributes: dict[str, Scalar] = {"stripe_event_type": event.type}
        if intent:
            attributes.update(self._attributes(intent))
        fields = self._fields(str(intent_id) if intent_id else None, attributes)
        build = BusinessEvent.error if reason in _ALERT_REASONS else BusinessEvent.denied
        self._events.log(build(names.STRIPE_EVENT_IGNORED, reason, **fields))

    def _replayed(self, event: StripeEvent, intent_id: UUID) -> None:
        self._events.log(
            BusinessEvent.success(
                names.STRIPE_EVENT_REPLAYED,
                **self._fields(str(intent_id), {"stripe_event_type": event.type}),
            )
        )

    @staticmethod
    def _fields(resource_id: str | None, attributes: Mapping[str, Scalar]) -> dict[str, object]:
        return {
            "resource_type": "payment_intent",
            "resource_id": resource_id,
            "actor_type": "service",
            "actor_id": "stripe",
            "attributes": attributes,
        }

    @staticmethod
    def _attributes(intent: PaymentIntent) -> dict[str, Scalar]:
        return {
            "order_reference": intent.order_reference,
            "amount_cents": intent.amount.cents,
            "currency": intent.currency.value,
        }
