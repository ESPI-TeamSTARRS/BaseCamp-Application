from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.core.logging.correlation import outbound_headers
from app.pay.domain.entities.checkout_session_ref import CheckoutSessionRef
from app.pay.domain.entities.payment_intent_ref import PaymentIntentRef
from app.pay.domain.exceptions.payment_rejected_error import PaymentRejectedError
from app.pay.domain.exceptions.payment_unavailable_error import PaymentUnavailableError
from app.pay.domain.interfaces.payment_gateway import PaymentGateway
from app.pay.domain.interfaces.service_token_provider import ServiceTokenProvider

HTTP_UNAUTHORIZED = 401
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
HTTP_UNPROCESSABLE = 422
SUCCESS_STATUSES = (200, 201)
REJECTION_STATUSES = (HTTP_NOT_FOUND, HTTP_CONFLICT, HTTP_UNPROCESSABLE)


class HttpPaymentGateway(PaymentGateway):
    """Adaptateur HTTP interne vers `payment` : délais courts, aucune fuite dans les erreurs.

    Le client `httpx` porte les délais (connexion, lecture, écriture, pool) : un `payment`
    arrêté ou saturé fait échouer l'appel vite, jamais bloquer la boutique.
    """

    def __init__(
        self,
        client: httpx.Client,
        tokens: ServiceTokenProvider,
        checkout_timeout: httpx.Timeout | None = None,
    ) -> None:
        self._client = client
        self._tokens = tokens
        # La création de la session passe par Stripe : `payment` peut mettre plus longtemps à répondre.
        self._checkout_timeout = checkout_timeout

    def create_intent(
        self, order_reference: str, amount_cents: int, currency: str, idempotency_key: str
    ) -> PaymentIntentRef:
        response = self._send(
            "POST",
            "/payments/intents",
            idempotency_key=idempotency_key,
            body={"order_reference": order_reference, "amount": amount_cents, "currency": currency},
        )
        return self._to_intent(response)

    def get_intent(self, intent_id: str) -> PaymentIntentRef:
        return self._to_intent(self._send("GET", f"/payments/intents/{quote(intent_id, safe='')}"))

    def confirm_intent(
        self, intent_id: str, payment_method: str, idempotency_key: str
    ) -> PaymentIntentRef:
        response = self._send(
            "POST",
            f"/payments/intents/{quote(intent_id, safe='')}/confirm",
            idempotency_key=idempotency_key,
            body={"payment_method": payment_method},
        )
        return self._to_intent(response)

    def create_checkout_session(self, intent_id: str) -> CheckoutSessionRef:
        response = self._send(
            "POST",
            f"/payments/intents/{quote(intent_id, safe='')}/checkout-session",
            timeout=self._checkout_timeout,
        )
        try:
            url = response.json()["checkout_url"]
        except (ValueError, KeyError, TypeError):
            raise PaymentUnavailableError from None
        if not isinstance(url, str) or not _is_stripe_page(url):
            raise PaymentUnavailableError  # jamais une adresse inattendue renvoyée au navigateur
        return CheckoutSessionRef(url)

    def _send(
        self,
        method: str,
        path: str,
        idempotency_key: str | None = None,
        body: dict[str, Any] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> httpx.Response:
        token = self._tokens.token()  # PaymentUnavailableError si `auth` n'en donne aucun
        headers = {**outbound_headers(), "Authorization": f"Bearer {token}"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        try:
            options: dict[str, Any] = {"timeout": timeout} if timeout is not None else {}
            response = self._client.request(method, path, json=body, headers=headers, **options)
        except httpx.HTTPError:
            raise PaymentUnavailableError from None
        if response.status_code in SUCCESS_STATUSES:
            return response
        if response.status_code in REJECTION_STATUSES:
            raise PaymentRejectedError(conflict=response.status_code == HTTP_CONFLICT)
        if response.status_code == HTTP_UNAUTHORIZED:
            self._tokens.discard(token)  # jeton refusé : le prochain appel en demande un nouveau
        raise PaymentUnavailableError  # 401, 429 (saturé), 5xx

    @staticmethod
    def _to_intent(response: httpx.Response) -> PaymentIntentRef:
        try:
            body = response.json()
            return PaymentIntentRef(
                id=str(body["id"]),
                status=str(body["status"]),
                amount_cents=int(body["amount"]),
                currency=str(body["currency"]),
            )
        except (ValueError, KeyError, TypeError):
            raise PaymentUnavailableError from None


def _is_stripe_page(url: str) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""
    return (
        parts.scheme == "https"
        and not parts.username
        and not parts.password
        and (host == "stripe.com" or host.endswith(".stripe.com"))
    )
