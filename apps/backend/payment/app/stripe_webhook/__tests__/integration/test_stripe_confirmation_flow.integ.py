"""Confirmation par le webhook Stripe signé (WORK-89), de bout en bout sur SQLite : route, base, outbox."""

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

from app.caller_identity.__tests__.tokens import SHOP_TOKEN
from app.core.config import Settings
from app.core.logging.structlog_business_event_logger import StructlogBusinessEventLogger
from app.intent.infrastructure.models.base import Base
from app.main import create_app
from app.ratelimit.infrastructure.models.base import Base as RateLimitBase

SECRET = "whsec_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
SESSION_ID = "cs_test_flow1"


@dataclass
class Env:
    client: TestClient
    engine: Engine
    log: io.StringIO

    def intent(self, order: str = "CMD-1") -> str:
        response = self.client.post(
            "/payments/intents",
            json={"order_reference": order, "amount": 4990, "currency": "EUR"},
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
        return intent_id

    def send(self, payload: dict[str, Any], secret: str = SECRET) -> Any:
        body = json.dumps(payload).encode()
        stamp = int(time.time())
        digest = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
        return self.client.post(
            "/stripe/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": f"t={stamp},v1={digest}",
            },
        )

    def status(self, intent_id: str) -> str:
        return str(
            self.client.get(
                f"/payments/intents/{intent_id}", headers={"Authorization": f"Bearer {SHOP_TOKEN}"}
            ).json()["status"]
        )

    def count(self, table: str) -> int:
        with self.engine.connect() as connection:
            return int(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())  # noqa: S608

    def logged(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.log.getvalue().splitlines() if line.strip()]

    def outbox_types(self) -> list[str]:
        with self.engine.connect() as connection:
            return [row[0] for row in connection.execute(text("SELECT event_type FROM outbox"))]


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
    settings = Settings(stripe_webhook_secret_file=str(secret_file))
    application = create_app(settings, engine, StructlogBusinessEventLogger("payment", log))
    with TestClient(application, raise_server_exceptions=False) as client:
        yield Env(client, engine, log)


def _completed(intent_id: str, event_id: str = "evt_1", **overrides: Any) -> dict[str, Any]:
    obj = {
        "id": SESSION_ID,
        "payment_status": "paid",
        "amount_total": 4990,
        "currency": "eur",
        "metadata": {"payment_intent_id": intent_id},
    }
    obj.update(overrides)
    return {"id": event_id, "type": "checkout.session.completed", "data": {"object": obj}}


class TestConfirmedByStripe:
    def test_a_paid_session_confirms_the_intent_writes_the_ledger_and_queues_the_shop_event(
        self, env: Env
    ) -> None:
        intent_id = env.intent()

        response = env.send(_completed(intent_id))

        assert (response.status_code, response.json()) == (200, {"received": True})
        assert env.status(intent_id) == "succeeded"
        assert env.count("ledger_entries") == 2
        assert env.outbox_types() == ["payment.succeeded"]
        assert env.count("stripe_events") == 1

    def test_a_replayed_event_has_no_second_effect_and_still_answers_200(self, env: Env) -> None:
        intent_id = env.intent()
        env.send(_completed(intent_id))

        replay = env.send(_completed(intent_id))

        assert replay.status_code == 200
        assert env.count("ledger_entries") == 2 and env.count("outbox") == 1
        assert env.count("stripe_events") == 1
        assert "stripe.event_replayed" in env.log.getvalue()

    def test_the_synchronous_confirmation_stays_available_in_simulated_mode(self, env: Env) -> None:
        intent_id = env.intent()

        env.send(_completed(intent_id))

        again = env.client.post(
            f"/payments/intents/{intent_id}/confirm",
            json={"payment_method": "pm_test_success"},
            headers={
                "Authorization": f"Bearer {SHOP_TOKEN}",
                "Idempotency-Key": "confirm-key-0001",
            },
        )
        assert again.status_code == 409  # déjà réglée : jamais de second paiement


class TestDiscrepancy:
    @pytest.mark.parametrize(
        "overrides",
        [{"amount_total": 1}, {"currency": "usd"}, {"id": "cs_test_autre"}],
    )
    def test_nothing_is_confirmed_and_the_alert_is_logged(
        self, env: Env, overrides: dict[str, Any]
    ) -> None:
        intent_id = env.intent()

        response = env.send(_completed(intent_id, **overrides))

        assert response.status_code == 200  # acquitté : Stripe ne doit pas réessayer sans fin
        assert env.status(intent_id) == "requires_confirmation"
        assert env.count("ledger_entries") == 0 and env.count("outbox") == 0
        (alert,) = [e for e in env.logged() if e["event"] == "stripe.event_ignored"]
        assert alert["level"] == "error"


class TestFailedPayment:
    def test_the_intent_fails_without_ledger_and_the_shop_is_told(self, env: Env) -> None:
        intent_id = env.intent()
        payload = {
            "id": "evt_fail",
            "type": "payment_intent.payment_failed",
            "data": {"object": {"id": "pi_test", "metadata": {"payment_intent_id": intent_id}}},
        }

        assert env.send(payload).status_code == 200

        assert env.status(intent_id) == "failed"
        assert env.count("ledger_entries") == 0
        assert env.outbox_types() == ["payment.failed"]


class TestRefusals:
    def test_a_bad_signature_reads_and_writes_nothing(self, env: Env) -> None:
        intent_id = env.intent()
        other = "whsec_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6"

        response = env.send(_completed(intent_id), secret=other)

        assert (response.status_code, response.json()) == (400, {"detail": "Invalid signature."})
        assert env.status(intent_id) == "requires_confirmation"
        assert env.count("stripe_events") == 0 and env.count("ledger_entries") == 0

    def test_logs_hold_neither_the_body_nor_the_secret(self, env: Env) -> None:
        intent_id = env.intent()
        env.send(_completed(intent_id))

        raw = env.log.getvalue()
        assert SECRET not in raw and SESSION_ID not in raw and "evt_1" not in raw
