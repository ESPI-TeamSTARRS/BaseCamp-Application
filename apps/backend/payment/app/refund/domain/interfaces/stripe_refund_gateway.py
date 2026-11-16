from abc import ABC, abstractmethod

from app.refund.domain.value_objects.created_stripe_refund import CreatedStripeRefund
from app.refund.domain.value_objects.stripe_refund_request import StripeRefundRequest


class StripeRefundGateway(ABC):
    """Rembourse un paiement chez Stripe. Lève StripeRefundUnavailableError sur tout échec."""

    @abstractmethod
    def create_refund(self, request: StripeRefundRequest) -> CreatedStripeRefund: ...
