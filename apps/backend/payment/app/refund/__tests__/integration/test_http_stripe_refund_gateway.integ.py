"""Adaptateur HTTP du remboursement Stripe (WORK-90) : testé contre un faux serveur local, jamais Stripe."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs
from uuid import UUID

import pytest
from pydantic import SecretStr

from app.refund.domain.exceptions.stripe_refund_unavailable_error import (
    StripeRefundUnavailableError,
)
from app.refund.domain.value_objects.stripe_refund_request import StripeRefundRequest
from app.refund.infrastructure.gateways.http_stripe_refund_gateway import HttpStripeRefundGateway

KEY = SecretStr("sk_" + "test_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4")
INTENT_ID = UUID("5b0f3a8e-4a35-4c7f-8d0e-2f2d6d8a9b10")


class FakeStripe:
    def __init__(self) -> None:
        self.status = 200
        self.body: bytes = json.dumps({"id": "re_3Test1", "status": "succeeded"}).encode()
        self.delay = 0.0
        self.received: dict[str, Any] = {}


@pytest.fixture
def stripe() -> Iterator[tuple[FakeStripe, str]]:
    fake = FakeStripe()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            fake.received = {
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "form": parse_qs(self.rfile.read(length).decode()),
            }
            if fake.delay:
                threading.Event().wait(fake.delay)
            self.send_response(fake.status)
            self.send_header("Content-Length", str(len(fake.body)))
            self.end_headers()
            self.wfile.write(fake.body)

        def log_message(self, *_: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield fake, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _request() -> StripeRefundRequest:
    return StripeRefundRequest(INTENT_ID, "pi_3Test1", 4000, "refund", "basecamp-refund-key-1")


def _gateway(base_url: str, timeout: float = 2.0) -> HttpStripeRefundGateway:
    return HttpStripeRefundGateway(base_url, KEY, timeout)


class TestRequest:
    def test_the_refund_asks_stripe_for_the_server_side_amount_on_the_stripe_payment(
        self, stripe: tuple[FakeStripe, str]
    ) -> None:
        fake, base_url = stripe

        created = _gateway(base_url).create_refund(_request())

        assert (created.refund_id, created.status, created.accepted) == (
            "re_3Test1",
            "succeeded",
            True,
        )
        assert fake.received["path"] == "/v1/refunds"
        form = fake.received["form"]
        assert form["payment_intent"] == ["pi_3Test1"]
        assert form["amount"] == ["4000"]
        assert form["metadata[payment_intent_id]"] == [str(INTENT_ID)]
        assert form["metadata[basecamp_origin]"] == ["refund"]

    def test_the_headers_carry_the_key_and_the_idempotency_key(
        self, stripe: tuple[FakeStripe, str]
    ) -> None:
        fake, base_url = stripe

        _gateway(base_url).create_refund(_request())

        headers = fake.received["headers"]
        assert headers["authorization"] == f"Bearer {KEY.get_secret_value()}"
        assert headers["idempotency-key"] == "basecamp-refund-key-1"

    @pytest.mark.parametrize(("status", "accepted"), [("pending", True), ("failed", False)])
    def test_only_succeeded_and_pending_refunds_are_accepted(
        self, stripe: tuple[FakeStripe, str], status: str, accepted: bool
    ) -> None:
        fake, base_url = stripe
        fake.body = json.dumps({"id": "re_3Test1", "status": status}).encode()

        assert _gateway(base_url).create_refund(_request()).accepted is accepted


class TestFailures:
    @pytest.mark.parametrize(
        ("status", "reason"),
        [
            (500, "stripe_server_error"),
            (429, "stripe_rate_limited"),
            (400, "stripe_rejected"),
            (402, "stripe_rejected"),
        ],
    )
    def test_an_error_status_becomes_an_unavailable_error_with_a_fixed_reason(
        self, stripe: tuple[FakeStripe, str], status: int, reason: str
    ) -> None:
        fake, base_url = stripe
        fake.status = status
        fake.body = json.dumps({"error": {"message": "Charge ch_leak already refunded"}}).encode()

        with pytest.raises(StripeRefundUnavailableError) as error:
            _gateway(base_url).create_refund(_request())

        assert error.value.reason == reason
        assert "leak" not in str(error.value)

    @pytest.mark.parametrize(
        "body",
        [
            b"not json",
            b"[]",
            b'{"status": "succeeded"}',
            b'{"id": "re_1"}',
            b'{"id": "ch_1", "status": "succeeded"}',
            b'{"id": "re_1", "status": "Succeeded!"}',
            b'{"id": 1, "status": "succeeded"}',
        ],
    )
    def test_an_invalid_response_is_refused(
        self, stripe: tuple[FakeStripe, str], body: bytes
    ) -> None:
        fake, base_url = stripe
        fake.body = body

        with pytest.raises(StripeRefundUnavailableError) as error:
            _gateway(base_url).create_refund(_request())

        assert error.value.reason == "stripe_invalid_response"

    def test_a_slow_stripe_times_out(self, stripe: tuple[FakeStripe, str]) -> None:
        fake, base_url = stripe
        fake.delay = 1.0

        with pytest.raises(StripeRefundUnavailableError) as error:
            _gateway(base_url, timeout=0.2).create_refund(_request())

        assert error.value.reason == "stripe_timeout"

    def test_an_unreachable_stripe_is_reported(self) -> None:
        with pytest.raises(StripeRefundUnavailableError) as error:
            _gateway("http://127.0.0.1:9", timeout=0.5).create_refund(_request())

        assert error.value.reason in {"stripe_unreachable", "stripe_timeout"}

    def test_a_base_url_that_is_not_https_or_loopback_is_refused(self) -> None:
        with pytest.raises(ValueError, match="stripe api base url"):
            _gateway("http://api.stripe.com")
