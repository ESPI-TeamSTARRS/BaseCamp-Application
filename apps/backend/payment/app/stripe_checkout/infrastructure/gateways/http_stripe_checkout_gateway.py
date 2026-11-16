import re
from urllib.parse import urlsplit

from pydantic import SecretStr

from app.core.stripe.stripe_api_error import StripeApiError
from app.core.stripe.stripe_http_client import StripeHttpClient
from app.stripe_checkout.domain.exceptions.stripe_checkout_unavailable_error import (
    StripeCheckoutUnavailableError,
)
from app.stripe_checkout.domain.interfaces.stripe_checkout_gateway import StripeCheckoutGateway
from app.stripe_checkout.domain.value_objects.checkout_session_request import (
    CheckoutSessionRequest,
)
from app.stripe_checkout.domain.value_objects.created_checkout_session import (
    CreatedCheckoutSession,
)

_SESSION_ID = re.compile(r"cs_test_[A-Za-z0-9_]{1,200}")


class HttpStripeCheckoutGateway(StripeCheckoutGateway):
    """Crée la session Checkout : POST /v1/checkout/sessions (transport commun : StripeHttpClient)."""

    def __init__(self, base_url: str, secret_key: SecretStr, timeout_seconds: float) -> None:
        self._client = StripeHttpClient(base_url, secret_key, timeout_seconds)

    def create_session(self, request: CheckoutSessionRequest) -> CreatedCheckoutSession:
        try:
            payload = self._client.post_form(
                "/v1/checkout/sessions", self._form(request), request.idempotency_key
            )
        except StripeApiError as error:
            raise StripeCheckoutUnavailableError(error.reason) from None
        session_id, url = payload.get("id"), payload.get("url")
        if (
            not isinstance(session_id, str)
            or not _SESSION_ID.fullmatch(session_id)
            or not isinstance(url, str)
            or not _is_stripe_page(url)
        ):
            raise StripeCheckoutUnavailableError("stripe_invalid_response")
        return CreatedCheckoutSession(session_id, url)

    @staticmethod
    def _form(request: CheckoutSessionRequest) -> dict[str, str]:
        return {
            "mode": "payment",
            "client_reference_id": request.order_reference,
            "success_url": request.success_url,
            "cancel_url": request.cancel_url,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": request.currency,
            "line_items[0][price_data][unit_amount]": str(request.amount_cents),
            "line_items[0][price_data][product_data][name]": f"Commande {request.order_reference}",
            "metadata[payment_intent_id]": str(request.intent_id),
            "payment_intent_data[metadata][payment_intent_id]": str(request.intent_id),
        }


def _is_stripe_page(url: str) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and (host == "stripe.com" or host.endswith(".stripe.com"))
