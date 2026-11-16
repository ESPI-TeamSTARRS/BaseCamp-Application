"""POST /stripe/webhook (WORK-87) : signature vérifiée avant tout effet, aucun état écrit.

La passerelle n'expose que `POST /api/stripe/webhook`; le service reçoit `/stripe/webhook`. Cette route n'a
pas d'authentification par jeton : seule la signature de Stripe fait foi.
"""

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
from jsonschema import Draft202012Validator
from sqlalchemy import Engine, StaticPool, create_engine, event, func, select, text

from app.core.config import Settings
from app.core.logging.structlog_business_event_logger import StructlogBusinessEventLogger
from app.intent.infrastructure.models.base import Base
from app.main import create_app
from app.ratelimit.infrastructure.models.base import Base as RateLimitBase

SECRET = "whsec_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
OTHER_SECRET = "whsec_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6"
BODY = b'{"id":"evt_test","type":"checkout.session.completed","data":{"object":{"id":"cs_test"}}}'
CORRELATION_ID = "7c1f0e52-3a0b-4f1e-9a52-1d6f7a9b2c10"
SCHEMA_PATH = Path(__file__).resolve().parents[7] / "docs/development/log-event.schema.json"
VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
REJECTION = {"detail": "Invalid signature."}


@dataclass
class Env:
    client: TestClient
    engine: Engine
    log: io.StringIO

    def events(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.log.getvalue().splitlines()]

    def stripe_events(self) -> list[dict[str, Any]]:
        return [e for e in self.events() if str(e.get("event", "")).startswith("stripe.")]

    def post(self, body: bytes = BODY, signature: str | None = "signed", **headers: str) -> Any:
        header = _sign(body) if signature == "signed" else signature
        values = {"Content-Type": "application/json", "X-Correlation-ID": CORRELATION_ID}
        if header is not None:
            values["Stripe-Signature"] = header
        return self.client.post("/stripe/webhook", content=body, headers={**values, **headers})


def _sign(body: bytes, secret: str = SECRET, timestamp: int | None = None) -> str:
    stamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def _engine() -> Engine:
    engine = create_engine(
        "sqlite+pysqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    event.listen(
        engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON")
    )
    return engine


def _env(settings: Settings) -> Iterator[Env]:
    engine = _engine()
    Base.metadata.create_all(engine)
    RateLimitBase.metadata.create_all(engine)
    log = io.StringIO()
    application = create_app(
        settings, engine, StructlogBusinessEventLogger(settings.service_name, log)
    )
    with TestClient(application, raise_server_exceptions=False) as client:
        yield Env(client, engine, log)


@pytest.fixture
def secret_file(tmp_path: Path) -> str:
    path = tmp_path / "stripe-webhook-secret"
    path.write_text(SECRET + "\n", encoding="utf-8")
    return str(path)


@pytest.fixture
def env(secret_file: str) -> Iterator[Env]:
    yield from _env(Settings(stripe_webhook_secret_file=secret_file))


@pytest.fixture
def unconfigured_env() -> Iterator[Env]:
    yield from _env(Settings())


@pytest.fixture
def small_limit_env(secret_file: str) -> Iterator[Env]:
    yield from _env(
        Settings(stripe_webhook_secret_file=secret_file, stripe_webhook_max_body_bytes=1024)
    )


class TestAcceptedWebhook:
    def test_a_correctly_signed_event_is_acknowledged_without_any_token(self, env: Env) -> None:
        response = env.post()

        assert (response.status_code, response.json()) == (200, {"received": True})
        (verified,) = env.stripe_events()
        assert (verified["event"], verified["outcome"]) == ("stripe.webhook_verified", "success")
        assert verified["attributes"]["body_bytes"] == len(BODY)

    def test_the_log_line_follows_the_common_schema_and_holds_nothing_sensitive(
        self, env: Env
    ) -> None:
        header = _sign(BODY)
        env.post(signature=header)

        raw = env.log.getvalue()
        assert not [error.message for e in env.events() for error in VALIDATOR.iter_errors(e)]
        assert SECRET not in raw and header not in raw
        assert header.split("v1=")[1] not in raw
        assert BODY.decode() not in raw and "cs_test" not in raw

    def test_nothing_is_written_to_the_database(self, env: Env) -> None:
        env.post()

        with env.engine.connect() as connection:
            tables = [table.name for table in Base.metadata.sorted_tables]
            counts = [
                connection.execute(select(func.count()).select_from(text(name))).scalar()
                for name in tables
            ]
        assert counts == [0] * len(tables)

    def test_an_event_larger_than_the_global_limit_but_within_the_webhook_one_is_accepted(
        self, env: Env
    ) -> None:
        body = json.dumps({"id": "evt_big", "padding": "x" * 30_000}).encode()

        assert env.post(body).status_code == 200


class TestRefusedWebhook:
    @pytest.mark.parametrize(
        ("signature", "reason"),
        [
            (None, "signature_missing"),
            ("n'importe quoi", "signature_malformed"),
            (f"t={int(time.time())},v1={'0' * 64}", "signature_mismatch"),
            (_sign(BODY, OTHER_SECRET), "signature_mismatch"),
            (_sign(BODY, timestamp=int(time.time()) - 3600), "timestamp_out_of_window"),
        ],
    )
    def test_any_bad_signature_gets_the_same_400_and_a_typed_denial(
        self, env: Env, signature: str | None, reason: str
    ) -> None:
        response = env.post(signature=signature)

        assert (response.status_code, response.json()) == (400, REJECTION)
        (denied,) = env.stripe_events()
        assert (denied["event"], denied["outcome"], denied["reason"]) == (
            "stripe.webhook_rejected",
            "denied",
            reason,
        )
        assert not [error.message for error in VALIDATOR.iter_errors(denied)]

    def test_a_refusal_never_echoes_the_body_the_signature_or_the_secret(self, env: Env) -> None:
        header = _sign(BODY, OTHER_SECRET)

        response = env.post(signature=header)

        for sensitive in (SECRET, OTHER_SECRET, header, header.split("v1=")[1], "cs_test"):
            assert sensitive not in response.text
            assert sensitive not in env.log.getvalue()

    def test_a_tampered_body_is_refused(self, env: Env) -> None:
        header = _sign(BODY)

        assert env.post(BODY.replace(b"evt_test", b"evt_evil"), signature=header).status_code == 400

    def test_without_a_configured_webhook_secret_everything_is_refused_the_same_way(
        self, unconfigured_env: Env
    ) -> None:
        response = unconfigured_env.post()

        assert (response.status_code, response.json()) == (400, REJECTION)
        (denied,) = unconfigured_env.stripe_events()
        assert denied["reason"] == "webhook_not_configured"

    def test_a_body_above_the_webhook_limit_is_refused_with_413_before_any_verification(
        self, small_limit_env: Env
    ) -> None:
        response = small_limit_env.post(b"x" * 4096)

        assert response.status_code == 413
        assert small_limit_env.stripe_events() == []


class TestOtherMethods:
    @pytest.mark.parametrize("method", ["GET", "PUT", "PATCH", "DELETE"])
    def test_a_method_other_than_post_is_refused_with_405_and_leaves_no_trace(
        self, env: Env, method: str
    ) -> None:
        response = env.client.request(
            method, "/stripe/webhook", headers={"Stripe-Signature": _sign(BODY)}
        )

        assert response.status_code == 405
        assert env.stripe_events() == []

    def test_the_route_does_not_exist_under_other_paths(self, env: Env) -> None:
        for path in ("/api/stripe/webhook", "/stripe/webhooks", "/payments/stripe/webhook"):
            assert env.client.post(path, content=BODY).status_code == 404
