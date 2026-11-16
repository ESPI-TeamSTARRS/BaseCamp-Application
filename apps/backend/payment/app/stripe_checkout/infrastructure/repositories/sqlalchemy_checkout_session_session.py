from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.intent.domain.entities.payment_intent import PaymentIntent
from app.intent.infrastructure.mappers.payment_intent_mapper import PaymentIntentMapper
from app.intent.infrastructure.models.payment_intent_model import PaymentIntentModel
from app.stripe_checkout.domain.entities.checkout_session import CheckoutSession
from app.stripe_checkout.domain.interfaces.checkout_session_session import CheckoutSessionSession
from app.stripe_checkout.infrastructure.models.checkout_session_model import CheckoutSessionModel


class SqlAlchemyCheckoutSessionSession(CheckoutSessionSession):
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_intent(self, intent_id: UUID) -> PaymentIntent | None:
        model = self._session.scalar(
            select(PaymentIntentModel).where(PaymentIntentModel.id == intent_id).with_for_update()
        )
        return PaymentIntentMapper.to_entity(model) if model else None

    def get_checkout_session(self, intent_id: UUID) -> CheckoutSession | None:
        model = self._session.get(CheckoutSessionModel, intent_id)
        if model is None:
            return None
        return CheckoutSession(model.intent_id, model.session_id, model.url, model.created_at)

    def add_checkout_session(self, checkout_session: CheckoutSession) -> None:
        self._session.add(
            CheckoutSessionModel(
                intent_id=checkout_session.intent_id,
                session_id=checkout_session.session_id,
                url=checkout_session.url,
                created_at=checkout_session.created_at,
            )
        )
