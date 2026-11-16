import hashlib
import hmac
import re
from collections.abc import Callable, Sequence

from app.stripe_webhook.domain.errors.invalid_stripe_signature_error import (
    InvalidStripeSignatureError,
)

MAX_HEADER_LENGTH = 1024
_MAX_TIMESTAMP_DIGITS = 12
_TIMESTAMP = re.compile(r"\d{1,12}")
_SIGNATURE = re.compile(r"[0-9a-fA-F]{64}")


class StripeSignatureVerifier:
    """Vérifie l'en-tête `Stripe-Signature: t=<secondes>,v1=<hex>[,v1=<hex>…]` d'un webhook Stripe.

    `v1 = HMAC-SHA256(secret, "<t>.<corps brut>")`, comparé en temps constant. Un des secrets
    configurés (rotation) et une des signatures `v1` suffisent; les schémas plus anciens (`v0`) et les
    champs inconnus sont ignorés. L'horodatage doit être dans la fenêtre de tolérance, vérifié après la
    signature : un événement valide mais trop vieux est un rejeu. Sans secret configuré, tout est refusé.
    """

    def __init__(
        self, secrets: Sequence[str], tolerance_seconds: int, clock: Callable[[], int]
    ) -> None:
        self._secrets = [secret for secret in secrets if secret]
        self._tolerance = tolerance_seconds
        self._clock = clock

    def verify(self, header: str | None, raw_body: bytes) -> None:
        if not self._secrets:
            raise InvalidStripeSignatureError("webhook_not_configured")
        if header is None or not header.strip():
            raise InvalidStripeSignatureError("signature_missing")
        timestamp, signatures = self._parse(header)
        if not any(
            self._matches(secret, timestamp, raw_body, signatures) for secret in self._secrets
        ):
            raise InvalidStripeSignatureError("signature_mismatch")
        if abs(self._clock() - timestamp) > self._tolerance:
            raise InvalidStripeSignatureError("timestamp_out_of_window")

    @staticmethod
    def _parse(header: str) -> tuple[int, list[str]]:
        if len(header) > MAX_HEADER_LENGTH:
            raise InvalidStripeSignatureError("signature_malformed")
        timestamp: str | None = None
        signatures: list[str] = []
        for part in header.split(","):
            name, separator, value = part.strip().partition("=")
            if not separator:
                continue
            if name == "t" and timestamp is None:
                timestamp = value
            elif name == "v1":
                signatures.append(value)
        if timestamp is None or not _TIMESTAMP.fullmatch(timestamp):
            raise InvalidStripeSignatureError("signature_malformed")
        if not signatures or not all(_SIGNATURE.fullmatch(value) for value in signatures):
            raise InvalidStripeSignatureError("signature_malformed")
        return int(timestamp), [value.lower() for value in signatures]

    @staticmethod
    def _matches(secret: str, timestamp: int, raw_body: bytes, signatures: list[str]) -> bool:
        expected = hmac.new(
            secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256
        ).hexdigest()
        return any(hmac.compare_digest(expected, candidate) for candidate in signatures)
