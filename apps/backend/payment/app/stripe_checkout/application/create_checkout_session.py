from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.core.events.business_event import BusinessEvent, Scalar
from app.core.events.business_event_logger import BusinessEventLogger
from app.intent.domain.entities.payment_intent import PaymentIntent
from app.intent.domain.enums.intent_status import IntentStatus
from app.intent.domain.exceptions.payment_intent_not_found_error import PaymentIntentNotFoundError
from app.stripe_checkout.application import stripe_checkout_event_names as names
from app.stripe_checkout.application.commands.create_checkout_session_command import (
    CreateCheckoutSessionCommand,
)
from app.stripe_checkout.application.dtos.checkout_session_view import CheckoutSessionView
from app.stripe_checkout.domain.entities.checkout_session import CheckoutSession
from app.stripe_checkout.domain.exceptions.intent_not_checkoutable_error import (
    IntentNotCheckoutableError,
)
from app.stripe_checkout.domain.exceptions.stripe_checkout_unavailable_error import (
    StripeCheckoutUnavailableError,
)
from app.stripe_checkout.domain.exceptions.stripe_not_enabled_error import StripeNotEnabledError
from app.stripe_checkout.domain.interfaces.checkout_session_unit_of_work import (
    CheckoutSessionUnitOfWork,
)
from app.stripe_checkout.domain.interfaces.stripe_checkout_gateway import StripeCheckoutGateway
from app.stripe_checkout.domain.value_objects.checkout_session_request import (
    CheckoutSessionRequest,
)

ORDER_REFERENCE_PLACEHOLDER = "{order_reference}"


class CreateCheckoutSessionUseCase:
    """Crée (ou rejoue) la session Checkout Stripe d'une intention encore en attente de paiement.

    Le montant et la devise viennent de l'intention stockée, jamais de la requête. Une intention a au
    plus une session : un rejeu rend la session stockée sans appeler Stripe. L'appel à Stripe se fait
    HORS transaction (jamais de verrou de ligne pendant un appel réseau); la clé d'idempotence Stripe,
    dérivée de l'intention, garantit qu'un appel concurrent ou une relance retombe sur la même session.
    L'intention n'est jamais modifiée : le paiement ne sera constaté que par le webhook signé de Stripe.
    """

    def __init__(
        self,
        unit_of_work: CheckoutSessionUnitOfWork,
        gateway: StripeCheckoutGateway | None,
        success_url: str,
        cancel_url: str,
        clock: Callable[[], datetime],
        events: BusinessEventLogger,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._gateway = gateway
        self._success_url = success_url
        self._cancel_url = cancel_url
        self._clock = clock
        self._events = events

    def execute(self, command: CreateCheckoutSessionCommand) -> CheckoutSessionView:
        if self._gateway is None:
            self._reject(command, "stripe_not_enabled", None)
            raise StripeNotEnabledError
        intent_id = self._parse(command)
        with self._unit_of_work.begin() as session:
            intent = session.lock_intent(intent_id)
            if intent is None:
                self._reject(command, "intent_not_found", str(intent_id))
                raise PaymentIntentNotFoundError
            self._require_waiting(command, intent)
            stored = session.get_checkout_session(intent_id)
            if stored:
                return self._replayed(command, intent, stored)
        request = self._request(intent)
        try:
            created = self._gateway.create_session(request)
        except StripeCheckoutUnavailableError as error:
            self._events.log(
                BusinessEvent.error(
                    names.CHECKOUT_SESSION_FAILED, error.reason, **self._fields(command, intent)
                )
            )
            raise
        with self._unit_of_work.begin() as session:
            locked = session.lock_intent(intent_id)
            if locked is None:  # impossible : une intention n'est jamais supprimée
                raise PaymentIntentNotFoundError
            self._require_waiting(command, locked)
            stored = session.get_checkout_session(intent_id)
            if stored:
                return self._replayed(command, locked, stored)
            session.add_checkout_session(
                CheckoutSession(intent_id, created.session_id, created.url, self._clock())
            )
        self._events.log(
            BusinessEvent.success(
                names.CHECKOUT_SESSION_CREATED,
                **self._fields(command, intent, stripe_session_id=created.session_id),
            )
        )
        return CheckoutSessionView(str(intent_id), created.session_id, created.url, True)

    def _parse(self, command: CreateCheckoutSessionCommand) -> UUID:
        try:
            return UUID(command.intent_id)
        except ValueError:
            self._reject(command, "intent_not_found", None)
            raise PaymentIntentNotFoundError from None

    def _require_waiting(
        self, command: CreateCheckoutSessionCommand, intent: PaymentIntent
    ) -> None:
        now = self._clock()
        if intent.can_be_confirmed(now):
            return
        overdue = intent.status is IntentStatus.REQUIRES_CONFIRMATION and intent.is_overdue(now)
        reason = "expired" if overdue else f"status_{intent.status.value}"
        self._reject(command, reason, str(intent.id), intent)
        raise IntentNotCheckoutableError

    def _request(self, intent: PaymentIntent) -> CheckoutSessionRequest:
        return CheckoutSessionRequest(
            intent_id=intent.id,
            order_reference=intent.order_reference,
            amount_cents=intent.amount.cents,
            currency=intent.currency.value.lower(),
            success_url=self._success_url.replace(
                ORDER_REFERENCE_PLACEHOLDER, intent.order_reference
            ),
            cancel_url=self._cancel_url.replace(
                ORDER_REFERENCE_PLACEHOLDER, intent.order_reference
            ),
            idempotency_key=f"basecamp-checkout-{intent.id}",
        )

    def _replayed(
        self, command: CreateCheckoutSessionCommand, intent: PaymentIntent, stored: CheckoutSession
    ) -> CheckoutSessionView:
        self._events.log(
            BusinessEvent.success(
                names.CHECKOUT_SESSION_REPLAYED,
                **self._fields(command, intent, stripe_session_id=stored.session_id),
            )
        )
        return CheckoutSessionView(str(intent.id), stored.session_id, stored.url, False)

    def _reject(
        self,
        command: CreateCheckoutSessionCommand,
        reason: str,
        intent_id: str | None,
        intent: PaymentIntent | None = None,
    ) -> None:
        fields = self._fields(command, intent) if intent else self._fields(command, None)
        if intent is None:
            fields["resource_id"] = intent_id
        self._events.log(BusinessEvent.denied(names.CHECKOUT_SESSION_REJECTED, reason, **fields))

    @staticmethod
    def _fields(
        command: CreateCheckoutSessionCommand,
        intent: PaymentIntent | None,
        **extra: Scalar,
    ) -> dict[str, object]:
        """Ressource et acteur de l'événement : jamais l'adresse Stripe ni la valeur reçue."""
        attributes: dict[str, Scalar] = (
            {
                "order_reference": intent.order_reference,
                "amount_cents": intent.amount.cents,
                "currency": intent.currency.value,
                **extra,
            }
            if intent
            else {}
        )
        return {
            "resource_type": "payment_intent",
            "resource_id": str(intent.id) if intent else None,
            "actor_type": "service",
            "actor_id": command.caller,
            "attributes": attributes,
        }
