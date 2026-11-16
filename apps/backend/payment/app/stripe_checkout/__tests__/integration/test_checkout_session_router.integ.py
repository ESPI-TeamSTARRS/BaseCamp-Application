"""POST /payments/intents/{id}/checkout-session (WORK-88) : route, base SQLite et doublure de Stripe."""

import io
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine, StaticPool, create_engine, event, text

from app.caller_identity.__tests__.tokens import SHOP_TOKEN
from app.core.config import Settings
from app.core.logging.structlog_business_event_logger import StructlogBusinessEventLogger
from app.intent.infrastructure.models.base import Base
from app.main import create_app
from app.ratelimit.infrastructure.models.base import Base as RateLimitBase
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

TOKEN = SHOP_TOKEN
TEST_KEY = "sk_" + "test_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
STRIPE_URL = "https://checkout.stripe.com/c/pay/cs_test_a1b2c3"


class FakeGateway(StripeCheckoutGateway):
    def __init__(self) -> None:
        self.requests: list[CheckoutSessionRequest] = []
        self.failure: Exception | None = None

    def create_session(self, request: CheckoutSessionRequest) -> CreatedCheckoutSession:
        self.requests.append(request)
        if self.failure:
            raise self.failure
        return CreatedCheckoutSession("cs_test_a1b2c3", STRIPE_URL)


@dataclass
class Env:
    client: TestClient
    engine: Engine
    log: io.StringIO
    gateway: FakeGateway


def _build(tmp_path: Path, stripe: bool) -> Iterator[Env]:
    engine = create_engine(
        "sqlite+pysqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    event.listen(
        engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON")
    )
    Base.metadata.create_all(engine)
    RateLimitBase.metadata.create_all(engine)
    log = io.StringIO()
    gateway = FakeGateway()
    if stripe:
        key_file = tmp_path / "key"
        key_file.write_text(TEST_KEY, encoding="utf-8")
        settings = Settings(
            payment_provider="stripe",
            stripe_secret_key_file=str(key_file),
            stripe_success_url="https://basecamp.example/commande/{order_reference}/merci",
            stripe_cancel_url="https://basecamp.example/commande/{order_reference}/annulee",
        )
    else:
        settings = Settings()
    application = create_app(
        settings,
        engine,
        StructlogBusinessEventLogger("payment", log),
        checkout_gateway=gateway if stripe else None,
    )
    with TestClient(application) as client:
        yield Env(client, engine, log, gateway)


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    yield from _build(tmp_path, stripe=True)


@pytest.fixture
def simulated_env(tmp_path: Path) -> Iterator[Env]:
    yield from _build(tmp_path, stripe=False)


def _headers(key: str = "key-00000001") -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": key}


def _create(env: Env, order: str = "CMD-1") -> str:
    response = env.client.post(
        "/payments/intents",
        json={"order_reference": order, "amount": 4990, "currency": "EUR"},
        headers=_headers(f"create-{order}-key"),
    )
    return str(response.json()["id"])


def _checkout(env: Env, intent_id: str, **extra: object) -> Response:
    response: Response = env.client.post(
        f"/payments/intents/{intent_id}/checkout-session",
        headers={"Authorization": f"Bearer {TOKEN}"},
        **extra,
    )
    return response


def _events(env: Env) -> list[dict[str, object]]:
    return [json.loads(line) for line in env.log.getvalue().splitlines() if line.strip()]


class TestCheckoutSessionRoute:
    def test_it_creates_a_session_then_replays_it(self, env: Env) -> None:
        intent_id = _create(env)

        first = _checkout(env, intent_id)
        second = _checkout(env, intent_id)

        assert first.status_code == 201
        assert first.json() == {
            "intent_id": intent_id,
            "session_id": "cs_test_a1b2c3",
            "checkout_url": STRIPE_URL,
        }
        assert second.status_code == 200
        assert second.json() == first.json()
        assert len(env.gateway.requests) == 1

    def test_the_amount_comes_from_the_intent_and_never_from_the_request(self, env: Env) -> None:
        intent_id = _create(env)

        response = _checkout(env, intent_id, json={"amount": 1})

        assert response.status_code == 201  # le corps n'est jamais lu
        assert env.gateway.requests[0].amount_cents == 4990

    def test_the_intent_is_unchanged_and_still_confirmable_state_wise(self, env: Env) -> None:
        intent_id = _create(env)

        _checkout(env, intent_id)

        with env.engine.connect() as connection:
            status = connection.execute(text("SELECT status FROM payment_intents")).scalar_one()
        assert status == "requires_confirmation"

    def test_it_needs_a_service_token(self, env: Env) -> None:
        intent_id = _create(env)

        response = env.client.post(f"/payments/intents/{intent_id}/checkout-session")

        assert response.status_code == 401
        assert env.gateway.requests == []

    def test_an_unknown_intent_is_404(self, env: Env) -> None:
        assert _checkout(env, "00000000-0000-0000-0000-000000000000").status_code == 404

    def test_a_canceled_intent_is_409(self, env: Env) -> None:
        intent_id = _create(env)
        env.client.post(f"/payments/intents/{intent_id}/cancel", headers=_headers())

        response = _checkout(env, intent_id)

        assert response.status_code == 409
        assert env.gateway.requests == []

    def test_a_stripe_failure_is_a_sanitized_503_and_the_intent_stays_usable(
        self, env: Env
    ) -> None:
        intent_id = _create(env)
        env.gateway.failure = StripeCheckoutUnavailableError("stripe_server_error")

        failed = _checkout(env, intent_id)
        env.gateway.failure = None
        retried = _checkout(env, intent_id)

        assert failed.status_code == 503
        assert failed.json() == {"detail": "Payment provider unavailable"}
        assert "Retry-After" in failed.headers
        assert retried.status_code == 201

    def test_without_stripe_the_route_is_404(self, simulated_env: Env) -> None:
        intent_id = _create(simulated_env)

        response = _checkout(simulated_env, intent_id)

        assert response.status_code == 404
        assert simulated_env.gateway.requests == []

    def test_logs_never_contain_the_stripe_url(self, env: Env) -> None:
        intent_id = _create(env)
        _checkout(env, intent_id)

        assert STRIPE_URL not in env.log.getvalue()
        names = {str(entry.get("event")) for entry in _events(env)}
        assert "stripe.checkout_session_created" in names


class TestSynchronousConfirmationInStripeMode:
    def test_confirming_by_the_api_never_marks_the_intent_paid(self, env: Env) -> None:
        intent_id = _create(env)

        response = env.client.post(
            f"/payments/intents/{intent_id}/confirm",
            json={"payment_method": "pm_test_success"},
            headers=_headers("confirm-key-0001"),
        )

        assert response.status_code == 409
        with env.engine.connect() as connection:
            status = connection.execute(text("SELECT status FROM payment_intents")).scalar_one()
        assert status == "requires_confirmation"
