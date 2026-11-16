from abc import ABC, abstractmethod
from uuid import UUID

from app.intent.domain.entities.payment_intent import PaymentIntent
from app.stripe_checkout.domain.entities.checkout_session import CheckoutSession


class CheckoutSessionSession(ABC):
    """Une transaction de base de données : l'intention lue est verrouillée jusqu'à la validation."""

    @abstractmethod
    def lock_intent(self, intent_id: UUID) -> PaymentIntent | None: ...

    @abstractmethod
    def get_checkout_session(self, intent_id: UUID) -> CheckoutSession | None: ...

    @abstractmethod
    def add_checkout_session(self, checkout_session: CheckoutSession) -> None: ...
