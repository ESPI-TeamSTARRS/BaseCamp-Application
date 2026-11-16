import re

from pydantic import SecretStr

from app.core.stripe.stripe_api_error import StripeApiError
from app.core.stripe.stripe_http_client import StripeHttpClient
from app.refund.domain.exceptions.stripe_refund_unavailable_error import (
    StripeRefundUnavailableError,
)
from app.refund.domain.interfaces.stripe_refund_gateway import StripeRefundGateway
from app.refund.domain.value_objects.created_stripe_refund import CreatedStripeRefund
from app.refund.domain.value_objects.stripe_refund_request import StripeRefundRequest

_REFUND_ID = re.compile(r"re_[A-Za-z0-9_]{1,200}")
_STATUS = re.compile(r"[a-z_]{1,32}")


class HttpStripeRefundGateway(StripeRefundGateway):
    """Crée le remboursement : POST /v1/refunds (transport commun : StripeHttpClient)."""

    def __init__(self, base_url: str, secret_key: SecretStr, timeout_seconds: float) -> None:
        self._client = StripeHttpClient(base_url, secret_key, timeout_seconds)

    def create_refund(self, request: StripeRefundRequest) -> CreatedStripeRefund:
        form = {
            "payment_intent": request.stripe_payment_intent_id,
            "amount": str(request.amount_cents),
            "metadata[payment_intent_id]": str(request.intent_id),
            "metadata[basecamp_origin]": request.origin,
        }
        try:
            payload = self._client.post_form("/v1/refunds", form, request.idempotency_key)
        except StripeApiError as error:
            raise StripeRefundUnavailableError(error.reason) from None
        refund_id, status = payload.get("id"), payload.get("status")
        if (
            not isinstance(refund_id, str)
            or not _REFUND_ID.fullmatch(refund_id)
            or not isinstance(status, str)
            or not _STATUS.fullmatch(status)
        ):
            raise StripeRefundUnavailableError("stripe_invalid_response")
        return CreatedStripeRefund(refund_id, status)
