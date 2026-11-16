from abc import ABC, abstractmethod

from app.stripe_checkout.domain.value_objects.checkout_session_request import (
    CheckoutSessionRequest,
)
from app.stripe_checkout.domain.value_objects.created_checkout_session import (
    CreatedCheckoutSession,
)


class StripeCheckoutGateway(ABC):
    """Crée une session Checkout chez Stripe. Lève StripeCheckoutUnavailableError sur tout échec."""

    @abstractmethod
    def create_session(self, request: CheckoutSessionRequest) -> CreatedCheckoutSession: ...
