"""Adaptateur HTTP de Stripe (WORK-88) : testé contre un faux serveur local, jamais contre Stripe."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs
from uuid import UUID

import pytest
from pydantic import SecretStr

from app.stripe_checkout.domain.exceptions.stripe_checkout_unavailable_error import (
    StripeCheckoutUnavailableError,
)
from app.stripe_checkout.domain.value_objects.checkout_session_request import (
    CheckoutSessionRequest,
)
from app.stripe_checkout.infrastructure.gateways.http_stripe_checkout_gateway import (
    HttpStripeCheckoutGateway,
)

KEY = SecretStr("sk_" + "test_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4")
INTENT_ID = UUID("5b0f3a8e-4a35-4c7f-8d0e-2f2d6d8a9b10")
GOOD_URL = "https://checkout.stripe.com/c/pay/cs_test_a1b2c3"


class FakeStripe:
    """Réponse programmable et trace de la dernière requête reçue."""

    def __init__(self) -> None:
        self.status = 200
        self.body: bytes = json.dumps({"id": "cs_test_a1b2c3", "url": GOOD_URL}).encode()
        self.headers: dict[str, str] = {}
        self.delay = 0.0
        self.received: dict[str, Any] = {}
        self.hits = 0


@pytest.fixture
def stripe() -> Iterator[tuple[FakeStripe, str]]:
    fake = FakeStripe()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            fake.hits += 1
            length = int(self.headers.get("Content-Length", "0"))
            fake.received = {
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "form": parse_qs(self.rfile.read(length).decode()),
            }
            if fake.delay:
                threading.Event().wait(fake.delay)
            self.send_response(fake.status)
            for name, value in fake.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(fake.body)))
            self.end_headers()
            self.wfile.write(fake.body)

        def log_message(self, *_: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fake, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _request(**overrides: Any) -> CheckoutSessionRequest:
    values: dict[str, Any] = {
        "intent_id": INTENT_ID,
        "order_reference": "CMD-1",
        "amount_cents": 4990,
        "currency": "eur",
        "success_url": "https://basecamp.example/commande/CMD-1/merci",
        "cancel_url": "https://basecamp.example/commande/CMD-1/annulee",
        "idempotency_key": f"basecamp-checkout-{INTENT_ID}",
    }
    values.update(overrides)
    return CheckoutSessionRequest(**values)


def _gateway(base_url: str, timeout: float = 2.0) -> HttpStripeCheckoutGateway:
    return HttpStripeCheckoutGateway(base_url, KEY, timeout)


class TestRequest:
    def test_the_session_is_created_with_the_server_side_amount(
        self, stripe: tuple[FakeStripe, str]
    ) -> None:
        fake, base_url = stripe

        created = _gateway(base_url).create_session(_request())

        assert (created.session_id, created.url) == ("cs_test_a1b2c3", GOOD_URL)
        assert fake.received["path"] == "/v1/checkout/sessions"
        form = fake.received["form"]
        assert form["mode"] == ["payment"]
        assert form["line_items[0][price_data][currency]"] == ["eur"]
        assert form["line_items[0][price_data][unit_amount]"] == ["4990"]
        assert form["line_items[0][quantity]"] == ["1"]
        assert form["client_reference_id"] == ["CMD-1"]
        assert form["metadata[payment_intent_id]"] == [str(INTENT_ID)]
        assert form["payment_intent_data[metadata][payment_intent_id]"] == [str(INTENT_ID)]
        assert form["success_url"] == ["https://basecamp.example/commande/CMD-1/merci"]
        assert form["cancel_url"] == ["https://basecamp.example/commande/CMD-1/annulee"]

    def test_the_headers_carry_the_key_the_idempotency_key_and_a_pinned_api_version(
        self, stripe: tuple[FakeStripe, str]
    ) -> None:
        fake, base_url = stripe

        _gateway(base_url).create_session(_request())

        headers = fake.received["headers"]
        assert headers["authorization"] == f"Bearer {KEY.get_secret_value()}"
        assert headers["idempotency-key"] == f"basecamp-checkout-{INTENT_ID}"
        assert headers["content-type"] == "application/x-www-form-urlencoded"
        assert headers["stripe-version"]


class TestFailures:
    @pytest.mark.parametrize(
        ("status", "reason"),
        [
            (500, "stripe_server_error"),
            (503, "stripe_server_error"),
            (429, "stripe_rate_limited"),
            (401, "stripe_rejected"),
            (400, "stripe_rejected"),
        ],
    )
    def test_an_error_status_becomes_an_unavailable_error_with_a_fixed_reason(
        self, stripe: tuple[FakeStripe, str], status: int, reason: str
    ) -> None:
        fake, base_url = stripe
        fake.status = status
        fake.body = json.dumps({"error": {"message": "Invalid API Key sk_test_leak"}}).encode()

        with pytest.raises(StripeCheckoutUnavailableError) as error:
            _gateway(base_url).create_session(_request())

        assert error.value.reason == reason
        assert "leak" not in str(error.value)

    @pytest.mark.parametrize(
        "body",
        [
            b"not json",
            b"[]",
            b'{"id": "cs_test_x"}',
            b'{"url": "https://checkout.stripe.com/c/pay/x"}',
            json.dumps({"id": "cs_test_x", "url": "http://checkout.stripe.com/x"}).encode(),
            json.dumps({"id": "cs_test_x", "url": "https://evil.example/x"}).encode(),
            json.dumps(
                {"id": "cs_test_x", "url": "https://checkout.stripe.com.evil.io/x"}
            ).encode(),
            json.dumps({"id": "", "url": GOOD_URL}).encode(),
            json.dumps({"id": 12, "url": GOOD_URL}).encode(),
        ],
    )
    def test_an_invalid_response_is_refused(
        self, stripe: tuple[FakeStripe, str], body: bytes
    ) -> None:
        fake, base_url = stripe
        fake.body = body

        with pytest.raises(StripeCheckoutUnavailableError) as error:
            _gateway(base_url).create_session(_request())

        assert error.value.reason == "stripe_invalid_response"

    def test_a_redirect_is_not_followed(self, stripe: tuple[FakeStripe, str]) -> None:
        fake, base_url = stripe
        fake.status = 302
        fake.headers = {"Location": "http://127.0.0.1:9/elsewhere"}
        fake.body = b""

        with pytest.raises(StripeCheckoutUnavailableError):
            _gateway(base_url).create_session(_request())

        assert fake.hits == 1

    def test_a_slow_stripe_times_out(self, stripe: tuple[FakeStripe, str]) -> None:
        fake, base_url = stripe
        fake.delay = 1.0

        with pytest.raises(StripeCheckoutUnavailableError) as error:
            _gateway(base_url, timeout=0.2).create_session(_request())

        assert error.value.reason == "stripe_timeout"

    def test_an_unreachable_stripe_is_reported(self) -> None:
        with pytest.raises(StripeCheckoutUnavailableError) as error:
            _gateway("http://127.0.0.1:9", timeout=0.5).create_session(_request())

        assert error.value.reason in {"stripe_unreachable", "stripe_timeout"}

    def test_an_oversized_response_is_refused(self, stripe: tuple[FakeStripe, str]) -> None:
        fake, base_url = stripe
        fake.body = b'{"id": "cs_test_x", "url": "' + b"a" * 200_000 + b'"}'

        with pytest.raises(StripeCheckoutUnavailableError) as error:
            _gateway(base_url).create_session(_request())

        assert error.value.reason == "stripe_invalid_response"


class TestConstruction:
    @pytest.mark.parametrize(
        "base_url", ["http://api.stripe.com", "ftp://api.stripe.com", "https://", "api.stripe.com"]
    )
    def test_a_base_url_that_is_not_https_or_loopback_is_refused(self, base_url: str) -> None:
        with pytest.raises(ValueError, match="stripe api base url"):
            _gateway(base_url)

    def test_the_default_base_url_is_stripe_over_https(self) -> None:
        assert _gateway("https://api.stripe.com")
