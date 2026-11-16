"""Signature des webhooks Stripe (WORK-87) : `Stripe-Signature: t=<secondes>,v1=<hex>[,v1=<hex>…]`.

`v1 = HMAC-SHA256(secret, "<t>.<corps brut>")`, comparaison en temps constant, horodatage dans une
fenêtre de tolérance. Les secrets d'essai sont assemblés à l'exécution (aucune clé écrite en dur).
"""

import hashlib
import hmac

import pytest

from app.stripe_webhook.domain.errors.invalid_stripe_signature_error import (
    InvalidStripeSignatureError,
)
from app.stripe_webhook.domain.services.stripe_signature_verifier import StripeSignatureVerifier

SECRET = "whsec_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
OTHER_SECRET = "whsec_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6"
NOW = 1_790_000_000
TOLERANCE = 300
BODY = b'{"id":"evt_test","type":"checkout.session.completed","data":{"object":{}}}'


def _digest(body: bytes, secret: str = SECRET, timestamp: int = NOW) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def _header(body: bytes = BODY, secret: str = SECRET, timestamp: int = NOW, extra: str = "") -> str:
    return f"t={timestamp},v1={_digest(body, secret, timestamp)}{extra}"


def _verifier(*secrets: str, now: int = NOW) -> StripeSignatureVerifier:
    return StripeSignatureVerifier(list(secrets) or [SECRET], TOLERANCE, lambda: now)


def _reason(verifier: StripeSignatureVerifier, header: str | None, body: bytes = BODY) -> str:
    with pytest.raises(InvalidStripeSignatureError) as error:
        verifier.verify(header, body)
    return error.value.reason


class TestValidSignature:
    def test_a_correct_signature_is_accepted(self) -> None:
        _verifier().verify(_header(), BODY)

    def test_the_older_schemes_and_unknown_fields_are_ignored(self) -> None:
        header = f"t={NOW},v0={'0' * 64},v1={_digest(BODY)},foo=bar"

        _verifier().verify(header, BODY)

    def test_any_one_of_several_v1_signatures_is_enough(self) -> None:
        header = f"t={NOW},v1={'0' * 64},v1={_digest(BODY)}"

        _verifier().verify(header, BODY)

    def test_a_rotated_secret_is_accepted_during_the_overlap(self) -> None:
        verifier = _verifier(SECRET, OTHER_SECRET)

        verifier.verify(_header(secret=OTHER_SECRET), BODY)
        verifier.verify(_header(secret=SECRET), BODY)

    @pytest.mark.parametrize("offset", [-TOLERANCE, 0, TOLERANCE])
    def test_the_edges_of_the_tolerance_window_are_accepted(self, offset: int) -> None:
        _verifier(now=NOW + offset).verify(_header(), BODY)


class TestRefusedSignature:
    @pytest.mark.parametrize("header", [None, "", "   "])
    def test_a_missing_header_is_refused(self, header: str | None) -> None:
        assert _reason(_verifier(), header) == "signature_missing"

    @pytest.mark.parametrize(
        "header",
        [
            "n'importe quoi",
            f"v1={_digest(BODY)}",
            f"t={NOW}",
            f"t=abc,v1={_digest(BODY)}",
            f"t={NOW},v1=pas-de-l-hexa",
            f"t={NOW},v1=",
            f"t={NOW},v0={_digest(BODY)}",
            "t=" + "9" * 400 + ",v1=" + "0" * 64,
            "x" * 5000,
        ],
    )
    def test_a_malformed_header_is_refused(self, header: str) -> None:
        assert _reason(_verifier(), header) == "signature_malformed"

    def test_a_signature_made_with_another_secret_is_refused(self) -> None:
        assert _reason(_verifier(), _header(secret=OTHER_SECRET)) == "signature_mismatch"

    def test_an_altered_body_is_refused(self) -> None:
        assert _reason(_verifier(), _header(), BODY + b" ") == "signature_mismatch"

    def test_a_signed_timestamp_that_was_changed_is_refused(self) -> None:
        header = f"t={NOW + 1},v1={_digest(BODY)}"

        assert _reason(_verifier(), header) == "signature_mismatch"

    @pytest.mark.parametrize("offset", [-(TOLERANCE + 1), TOLERANCE + 1, -86_400])
    def test_a_timestamp_out_of_the_window_is_refused_even_if_correctly_signed(
        self, offset: int
    ) -> None:
        assert _reason(_verifier(now=NOW + offset), _header()) == "timestamp_out_of_window"

    def test_without_any_configured_secret_everything_is_refused(self) -> None:
        verifier = StripeSignatureVerifier([], TOLERANCE, lambda: NOW)

        assert _reason(verifier, _header()) == "webhook_not_configured"
        assert _reason(verifier, None) == "webhook_not_configured"

    def test_a_refusal_never_carries_the_secret_the_body_or_the_header(self) -> None:
        header = _header(secret=OTHER_SECRET)
        with pytest.raises(InvalidStripeSignatureError) as error:
            _verifier().verify(header, BODY)

        text = f"{error.value!s} {error.value!r}"
        assert SECRET not in text and OTHER_SECRET not in text
        assert header not in text and BODY.decode() not in text
