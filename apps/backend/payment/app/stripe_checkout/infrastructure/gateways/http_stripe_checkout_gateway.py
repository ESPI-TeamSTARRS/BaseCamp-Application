import json
import re
import socket
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode, urlsplit

from pydantic import SecretStr

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

STRIPE_API_VERSION = "2025-09-30.clover"
MAX_RESPONSE_BYTES = 65_536
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_SESSION_ID = re.compile(r"cs_test_[A-Za-z0-9_]{1,200}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Un 3xx n'est jamais suivi : la clé secrète ne doit partir que vers l'adresse configurée."""

    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None


class HttpStripeCheckoutGateway(StripeCheckoutGateway):
    """Appelle l'API REST de Stripe (mode test) : POST /v1/checkout/sessions.

    Bibliothèque standard seulement (une dépendance de plus pour un seul appel n'apporte rien), sans
    proxy ni redirection, avec un délai borné. L'adresse de base est `https` (ou boucle locale pour les
    essais). Les erreurs sont réduites à un code fixe : ni corps de réponse, ni clé dans les messages.
    """

    def __init__(self, base_url: str, secret_key: SecretStr, timeout_seconds: float) -> None:
        parts = urlsplit(base_url)
        secure = parts.scheme == "https" or (
            parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS
        )
        if not secure or not parts.hostname or parts.username or parts.password:
            raise ValueError("stripe api base url must be https (or loopback http)")
        self._endpoint = base_url.rstrip("/") + "/v1/checkout/sessions"
        self._secret_key = secret_key
        self._timeout = timeout_seconds
        # Sans ProxyHandler : l'environnement (HTTP_PROXY...) n'est jamais consulté.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)

    def create_session(self, request: CheckoutSessionRequest) -> CreatedCheckoutSession:
        http_request = urllib.request.Request(  # noqa: S310  # adresse https validée au constructeur
            self._endpoint,
            data=urlencode(self._form(request)).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._secret_key.get_secret_value()}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Idempotency-Key": request.idempotency_key,
                "Stripe-Version": STRIPE_API_VERSION,
                "Accept": "application/json",
            },
        )
        try:
            with self._opener.open(http_request, timeout=self._timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            raise StripeCheckoutUnavailableError(self._reason(error.code)) from None
        except TimeoutError:
            raise StripeCheckoutUnavailableError("stripe_timeout") from None
        except urllib.error.URLError as error:
            timed_out = isinstance(error.reason, TimeoutError | socket.timeout)
            raise StripeCheckoutUnavailableError(
                "stripe_timeout" if timed_out else "stripe_unreachable"
            ) from None
        except OSError:
            raise StripeCheckoutUnavailableError("stripe_unreachable") from None
        return self._parse(body)

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

    @staticmethod
    def _reason(status: int) -> str:
        if status == 429:
            return "stripe_rate_limited"
        if status >= 500:
            return "stripe_server_error"
        return "stripe_rejected"

    @staticmethod
    def _parse(body: bytes) -> CreatedCheckoutSession:
        invalid = StripeCheckoutUnavailableError("stripe_invalid_response")
        if len(body) > MAX_RESPONSE_BYTES:
            raise invalid
        try:
            payload = json.loads(body)
        except ValueError:
            raise invalid from None
        if not isinstance(payload, dict):
            raise invalid
        session_id, url = payload.get("id"), payload.get("url")
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            raise invalid
        if not isinstance(url, str) or not _is_stripe_page(url):
            raise invalid
        return CreatedCheckoutSession(session_id, url)


def _is_stripe_page(url: str) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and (host == "stripe.com" or host.endswith(".stripe.com"))
