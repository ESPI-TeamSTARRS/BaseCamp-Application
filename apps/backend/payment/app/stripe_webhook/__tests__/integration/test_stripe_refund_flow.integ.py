"""Remboursement d'un paiement Stripe (WORK-90), de bout en bout sur SQLite : paiement, remboursement, événements."""

import hashlib
import hmac
import io
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, StaticPool, create_engine, event, text

from app.caller_identity.__tests__.tokens import REFUND_TOKEN, SHOP_TOKEN
from app.core.config import Settings
from app.core.logging.structlog_business_event_logger import StructlogBusinessEventLogger
from app.intent.infrastructure.models.base import Base
from app.main import create_app
from app.ratelimit.infrastructure.models.base import Base as RateLimitBase
from app.refund.domain.exceptions.stripe_refund_unavailable_error import (
    StripeRefundUnavailableError,
)
from app.refund.domain.interfaces.stripe_refund_gateway import StripeRefundGateway
from app.refund.domain.value_objects.created_stripe_refund import CreatedStripeRefund
from app.refund.domain.value_objects.stripe_refund_request import StripeRefundRequest

SECRET = "whsec_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
SESSION_ID = "cs_test_refund1"
STRIPE_PAYMENT = "pi_3Refund1"


class FakeRefunds(StripeRefundGateway):
    def __init__(self) -> None:
        self.requests: list[StripeRefundRequest] = []
        self.failure: Exception | None = None
        self.counter = 0

    def create_refund(self, request: StripeRefundRequest) -> CreatedStripeRefund:
        self.requests.append(request)
        if self.failure:
            raise self.failure
        self.counter += 1
        return CreatedStripeRefund(f"re_{self.counter}", "succeeded")


@dataclass
class Env:
    client: TestClient
    engine: Engine
    log: io.StringIO
    stripe: FakeRefunds

    def paid_intent(self, order: str = "CMD-1") -> str:
        response = self.client.post(
            "/payments/intents",
            json={"order_reference": order, "amount": 10000, "currency": "EUR"},
            headers={"Authorization": f"Bearer {SHOP_TOKEN}", "Idempotency-Key": f"create-{order}"},
        )
        intent_id = str(response.json()["id"])
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO stripe_checkout_sessions (intent_id, session_id, url, created_at) "
                    "VALUES (:intent, :session, 'https://checkout.stripe.com/c/pay/x', CURRENT_TIMESTAMP)"
                ),
                {"intent": intent_id.replace("-", ""), "session": SESSION_ID},
            )
        self.send(_completed(intent_id))
        return intent_id

    def send(self, payload: dict[str, Any]) -> Any:
        body = json.dumps(payload).encode()
        stamp = int(time.time())
        digest = hmac.new(SECRET.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
        return self.client.post(
            "/stripe/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": f"t={stamp},v1={digest}",
            },
        )

    def refund(self, intent_id: str, amount: int = 4000, key: str = "refund-key-0001") -> Any:
        return self.client.post(
            f"/payments/intents/{intent_id}/refunds",
            json={"amount": amount},
            headers={"Authorization": f"Bearer {REFUND_TOKEN}", "Idempotency-Key": key},
        )

    def scalar(self, sql: str) -> Any:
        with self.engine.connect() as connection:
            return connection.execute(text(sql)).scalar_one()

    def logged(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.log.getvalue().splitlines() if line.strip()]


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    secret_file = tmp_path / "whsec"
    secret_file.write_text(SECRET, encoding="utf-8")
    engine = create_engine(
        "sqlite+pysqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    event.listen(
        engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON")
    )
    Base.metadata.create_all(engine)
    RateLimitBase.metadata.create_all(engine)
    log = io.StringIO()
    stripe = FakeRefunds()
    application = create_app(
        Settings(stripe_webhook_secret_file=str(secret_file)),
        engine,
        StructlogBusinessEventLogger("payment", log),
        refund_gateway=stripe,
    )
    with TestClient(application, raise_server_exceptions=False) as client:
        yield Env(client, engine, log, stripe)


def _completed(intent_id: str, event_id: str = "evt_pay") -> dict[str, Any]:
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": SESSION_ID,
                "payment_status": "paid",
                "amount_total": 10000,
                "currency": "eur",
                "payment_intent": STRIPE_PAYMENT,
                "metadata": {"payment_intent_id": intent_id},
            }
        },
    }


class TestRefundOfAStripePayment:
    def test_the_refund_is_made_at_stripe_then_booked_once(self, env: Env) -> None:
        intent_id = env.paid_intent()

        response = env.refund(intent_id)

        assert response.status_code == 201
        assert env.stripe.requests[0].stripe_payment_intent_id == STRIPE_PAYMENT
        assert env.stripe.requests[0].amount_cents == 4000
        assert env.scalar("SELECT refunded_amount FROM payment_intents") == 4000
        assert env.scalar("SELECT stripe_refund_id FROM refunds") == "re_1"
        assert env.scalar("SELECT count(*) FROM ledger_entries") == 4  # paiement + remboursement

    def test_a_replay_with_the_same_key_never_asks_stripe_again(self, env: Env) -> None:
        intent_id = env.paid_intent()
        first = env.refund(intent_id)

        replay = env.refund(intent_id)

        assert (first.status_code, replay.status_code) == (201, 200)
        assert replay.json()["id"] == first.json()["id"]
        assert len(env.stripe.requests) == 1
        assert env.scalar("SELECT count(*) FROM refunds") == 1

    def test_cumulated_refunds_never_exceed_the_captured_amount(self, env: Env) -> None:
        intent_id = env.paid_intent()
        env.refund(intent_id, 6000, "refund-key-0001")

        over = env.refund(intent_id, 5000, "refund-key-0002")

        assert over.status_code == 409
        assert len(env.stripe.requests) == 1

    def test_a_stripe_failure_is_a_sanitized_503_and_the_request_can_be_replayed(
        self, env: Env
    ) -> None:
        intent_id = env.paid_intent()
        env.stripe.failure = StripeRefundUnavailableError("stripe_server_error")

        failed = env.refund(intent_id)
        env.stripe.failure = None
        retried = env.refund(intent_id)

        assert failed.status_code == 503
        assert failed.json() == {"detail": "Payment provider unavailable"}
        assert "stripe_server_error" not in failed.text
        assert retried.status_code == 201
        assert env.scalar("SELECT refunded_amount FROM payment_intents") == 4000
        assert env.stripe.requests[0].idempotency_key == env.stripe.requests[1].idempotency_key

    def test_a_failed_refund_leaves_the_books_untouched(self, env: Env) -> None:
        intent_id = env.paid_intent()
        env.stripe.failure = StripeRefundUnavailableError("stripe_timeout")

        env.refund(intent_id)

        assert env.scalar("SELECT refunded_amount FROM payment_intents") == 0
        assert env.scalar("SELECT count(*) FROM refunds") == 0
        assert env.scalar("SELECT count(*) FROM ledger_entries") == 2
        assert any(e["event"] == "refund.stripe_failed" for e in env.logged())


class TestRefundEvent:
    def _refund_event(self, intent_id: str, stripe_id: str, status: str = "succeeded") -> Any:
        return {
            "id": f"evt_{stripe_id}_{status}",
            "type": "refund.updated",
            "data": {
                "object": {
                    "id": stripe_id,
                    "status": status,
                    "metadata": {"payment_intent_id": intent_id},
                }
            },
        }

    def test_the_stripe_event_confirms_the_refund_without_a_second_ledger_write(
        self, env: Env
    ) -> None:
        intent_id = env.paid_intent()
        env.refund(intent_id)

        response = env.send(self._refund_event(intent_id, "re_1"))

        assert response.status_code == 200
        assert env.scalar("SELECT count(*) FROM ledger_entries") == 4
        assert env.scalar("SELECT count(*) FROM refunds") == 1
        assert any(e["event"] == "stripe.refund_confirmed" for e in env.logged())

    def test_a_refund_that_failed_at_stripe_is_an_alert_and_changes_nothing(self, env: Env) -> None:
        intent_id = env.paid_intent()
        env.refund(intent_id)

        env.send(self._refund_event(intent_id, "re_1", "failed"))

        alert = next(e for e in env.logged() if e["event"] == "stripe.refund_failed")
        assert alert["level"] == "error"
        assert env.scalar("SELECT refunded_amount FROM payment_intents") == 4000


class TestLatePayment:
    def test_a_second_card_paying_a_failed_intent_is_refunded_without_touching_the_ledger(
        self, env: Env
    ) -> None:
        response = env.client.post(
            "/payments/intents",
            json={"order_reference": "CMD-2", "amount": 10000, "currency": "EUR"},
            headers={"Authorization": f"Bearer {SHOP_TOKEN}", "Idempotency-Key": "create-late-1"},
        )
        intent_id = str(response.json()["id"])
        with env.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO stripe_checkout_sessions (intent_id, session_id, url, created_at) "
                    "VALUES (:intent, :session, 'https://checkout.stripe.com/c/pay/x', CURRENT_TIMESTAMP)"
                ),
                {"intent": intent_id.replace("-", ""), "session": SESSION_ID},
            )
        env.send(
            {
                "id": "evt_fail",
                "type": "payment_intent.payment_failed",
                "data": {"object": {"id": "pi_1", "metadata": {"payment_intent_id": intent_id}}},
            }
        )

        late = env.send(_completed(intent_id, "evt_late"))

        assert late.status_code == 200
        (request,) = env.stripe.requests
        assert (request.origin, request.amount_cents) == ("late_payment", 10000)
        assert env.scalar("SELECT status FROM payment_intents") == "failed"
        assert env.scalar("SELECT count(*) FROM ledger_entries") == 0
        assert any(e["event"] == "stripe.late_payment_refunded" for e in env.logged())

    def test_when_stripe_fails_the_event_is_answered_503_so_that_stripe_retries_it(
        self, env: Env
    ) -> None:
        response = env.client.post(
            "/payments/intents",
            json={"order_reference": "CMD-3", "amount": 10000, "currency": "EUR"},
            headers={"Authorization": f"Bearer {SHOP_TOKEN}", "Idempotency-Key": "create-late-2"},
        )
        intent_id = str(response.json()["id"])
        with env.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO stripe_checkout_sessions (intent_id, session_id, url, created_at) "
                    "VALUES (:intent, :session, 'https://checkout.stripe.com/c/pay/x', CURRENT_TIMESTAMP)"
                ),
                {"intent": intent_id.replace("-", ""), "session": SESSION_ID},
            )
        env.client.post(
            f"/payments/intents/{intent_id}/cancel",
            headers={"Authorization": f"Bearer {SHOP_TOKEN}"},
        )
        env.stripe.failure = StripeRefundUnavailableError("stripe_timeout")

        failed = env.send(_completed(intent_id, "evt_retry"))
        env.stripe.failure = None
        retried = env.send(_completed(intent_id, "evt_retry"))

        assert (failed.status_code, retried.status_code) == (503, 200)
        assert env.scalar("SELECT count(*) FROM stripe_events") == 1
