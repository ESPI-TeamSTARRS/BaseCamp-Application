from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.intent.domain.entities.payment_intent import PaymentIntent
from app.intent.infrastructure.mappers.payment_intent_mapper import PaymentIntentMapper
from app.intent.infrastructure.models.payment_intent_model import PaymentIntentModel
from app.ledger.domain.entities.ledger_transaction import LedgerTransaction
from app.ledger.domain.interfaces.ledger_repository import LedgerRepository
from app.refund.infrastructure.models.refund_model import RefundModel
from app.stripe_checkout.infrastructure.models.checkout_session_model import CheckoutSessionModel
from app.stripe_webhook.domain.entities.processed_stripe_event import ProcessedStripeEvent
from app.stripe_webhook.domain.errors.duplicate_stripe_event_error import (
    DuplicateStripeEventError,
)
from app.stripe_webhook.domain.interfaces.stripe_event_session import StripeEventSession
from app.stripe_webhook.infrastructure.models.stripe_event_model import StripeEventModel
from app.webhook.domain.entities.outbox_event import OutboxEvent
from app.webhook.domain.interfaces.outbox_repository import OutboxRepository


class SqlAlchemyStripeEventSession(StripeEventSession):
    def __init__(
        self, session: Session, ledger: LedgerRepository, outbox: OutboxRepository
    ) -> None:
        self._session = session
        self._ledger = ledger
        self._outbox = outbox

    def is_processed(self, event_id: str) -> bool:
        return self._session.get(StripeEventModel, event_id) is not None

    def lock_intent(self, intent_id: UUID) -> PaymentIntent | None:
        model = self._session.scalar(
            select(PaymentIntentModel).where(PaymentIntentModel.id == intent_id).with_for_update()
        )
        return PaymentIntentMapper.to_entity(model) if model else None

    def get_checkout_session_id(self, intent_id: UUID) -> str | None:
        model = self._session.get(CheckoutSessionModel, intent_id)
        return model.session_id if model else None

    def save_payment_reference(self, intent_id: UUID, stripe_payment_intent_id: str) -> None:
        model = self._session.get(CheckoutSessionModel, intent_id)
        if model is not None:
            model.stripe_payment_intent_id = stripe_payment_intent_id

    def has_stripe_refund(self, stripe_refund_id: str) -> bool:
        return (
            self._session.scalar(
                select(RefundModel.id).where(RefundModel.stripe_refund_id == stripe_refund_id)
            )
            is not None
        )

    def record_event(self, event: ProcessedStripeEvent) -> None:
        self._session.add(
            StripeEventModel(
                event_id=event.event_id,
                event_type=event.event_type,
                outcome=event.outcome,
                intent_id=event.intent_id,
                processed_at=event.processed_at,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as error:
            if "stripe_events" in str(error.orig):
                raise DuplicateStripeEventError from None
            raise

    def save_settlement(
        self, intent: PaymentIntent, ledger: LedgerTransaction | None, outbox_event: OutboxEvent
    ) -> None:
        model = self._session.get(PaymentIntentModel, intent.id)
        if model is None:  # impossible : l'intention est verrouillée par la même transaction
            raise LookupError
        model.status = intent.status.value
        if ledger:
            self._ledger.add(ledger)
        self._outbox.add(outbox_event)
