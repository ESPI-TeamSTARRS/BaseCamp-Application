"""Session Checkout Stripe (WORK-88) : cas d'usage, avec une doublure de Stripe et une base en mémoire."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.core.__tests__.in_memory_business_event_logger import InMemoryBusinessEventLogger
from app.core.events.outcome import Outcome
from app.intent.domain.entities.payment_intent import PaymentIntent
from app.intent.domain.enums.currency import Currency
from app.intent.domain.enums.intent_status import IntentStatus
from app.intent.domain.exceptions.payment_intent_not_found_error import PaymentIntentNotFoundError
from app.intent.domain.value_objects.amount import Amount
from app.stripe_checkout.application import stripe_checkout_event_names as names
from app.stripe_checkout.application.commands.create_checkout_session_command import (
    CreateCheckoutSessionCommand,
)
from app.stripe_checkout.application.create_checkout_session import CreateCheckoutSessionUseCase
from app.stripe_checkout.domain.entities.checkout_session import CheckoutSession
from app.stripe_checkout.domain.exceptions.intent_not_checkoutable_error import (
    IntentNotCheckoutableError,
)
from app.stripe_checkout.domain.exceptions.stripe_checkout_unavailable_error import (
    StripeCheckoutUnavailableError,
)
from app.stripe_checkout.domain.exceptions.stripe_not_enabled_error import StripeNotEnabledError
from app.stripe_checkout.domain.interfaces.checkout_session_session import CheckoutSessionSession
from app.stripe_checkout.domain.interfaces.checkout_session_unit_of_work import (
    CheckoutSessionUnitOfWork,
)
from app.stripe_checkout.domain.interfaces.stripe_checkout_gateway import StripeCheckoutGateway
from app.stripe_checkout.domain.value_objects.checkout_session_request import (
    CheckoutSessionRequest,
)
from app.stripe_checkout.domain.value_objects.created_checkout_session import (
    CreatedCheckoutSession,
)

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
SUCCESS_URL = "https://basecamp.example/commande/{order_reference}/merci"
CANCEL_URL = "https://basecamp.example/commande/{order_reference}/annulee"
STRIPE_URL = "https://checkout.stripe.com/c/pay/cs_test_a1b2c3"


class InMemorySession(CheckoutSessionSession):
    def __init__(
        self, intents: dict[UUID, PaymentIntent], sessions: dict[UUID, CheckoutSession]
    ) -> None:
        self._intents = intents
        self._sessions = sessions

    def lock_intent(self, intent_id: UUID) -> PaymentIntent | None:
        return self._intents.get(intent_id)

    def get_checkout_session(self, intent_id: UUID) -> CheckoutSession | None:
        return self._sessions.get(intent_id)

    def add_checkout_session(self, checkout_session: CheckoutSession) -> None:
        self._sessions[checkout_session.intent_id] = checkout_session


class InMemoryUnitOfWork(CheckoutSessionUnitOfWork):
    def __init__(
        self, intents: dict[UUID, PaymentIntent], sessions: dict[UUID, CheckoutSession]
    ) -> None:
        self._intents = intents
        self._sessions = sessions

    @contextmanager
    def begin(self) -> Iterator[CheckoutSessionSession]:
        yield InMemorySession(self._intents, self._sessions)


class FakeGateway(StripeCheckoutGateway):
    def __init__(self) -> None:
        self.requests: list[CheckoutSessionRequest] = []
        self.failure: Exception | None = None
        self.on_call: Callable[[], None] | None = None

    def create_session(self, request: CheckoutSessionRequest) -> CreatedCheckoutSession:
        self.requests.append(request)
        if self.on_call:
            self.on_call()
        if self.failure:
            raise self.failure
        return CreatedCheckoutSession(session_id="cs_test_a1b2c3", url=STRIPE_URL)


class Fixture:
    def __init__(self) -> None:
        self.intent = PaymentIntent.create(
            "CMD-1", Amount(4990), Currency.EUR, NOW - timedelta(minutes=1), timedelta(minutes=30)
        )
        self.intents = {self.intent.id: self.intent}
        self.sessions: dict[UUID, CheckoutSession] = {}
        self.gateway = FakeGateway()
        self.logger = InMemoryBusinessEventLogger()
        self.use_case = self.build(self.gateway)

    def build(self, gateway: StripeCheckoutGateway | None) -> CreateCheckoutSessionUseCase:
        return CreateCheckoutSessionUseCase(
            InMemoryUnitOfWork(self.intents, self.sessions),
            gateway,
            SUCCESS_URL,
            CANCEL_URL,
            lambda: NOW,
            self.logger,
        )

    def with_status(self, status: IntentStatus) -> None:
        self.intent = replace(self.intent, status=status)
        self.intents[self.intent.id] = self.intent

    def command(self, **overrides: object) -> CreateCheckoutSessionCommand:
        values: dict[str, object] = {"caller": "shop", "intent_id": str(self.intent.id)}
        values.update(overrides)
        return CreateCheckoutSessionCommand(**values)  # type: ignore[arg-type]

    def summary(self) -> list[tuple[str, Outcome, str | None]]:
        return [(e.event, e.outcome, e.reason) for e in self.logger.events]


@pytest.fixture
def fx() -> Fixture:
    return Fixture()


class TestCreation:
    def test_a_session_is_created_for_the_amount_stored_on_the_intent(self, fx: Fixture) -> None:
        view = fx.use_case.execute(fx.command())

        assert view.created is True
        assert (view.intent_id, view.session_id, view.url) == (
            str(fx.intent.id),
            "cs_test_a1b2c3",
            STRIPE_URL,
        )
        (request,) = fx.gateway.requests
        assert (request.amount_cents, request.currency) == (4990, "eur")
        assert request.order_reference == "CMD-1"
        assert request.intent_id == fx.intent.id

    def test_the_return_urls_carry_the_order_reference(self, fx: Fixture) -> None:
        fx.use_case.execute(fx.command())

        (request,) = fx.gateway.requests
        assert request.success_url == "https://basecamp.example/commande/CMD-1/merci"
        assert request.cancel_url == "https://basecamp.example/commande/CMD-1/annulee"

    def test_the_stripe_idempotency_key_is_derived_from_the_intent(self, fx: Fixture) -> None:
        fx.use_case.execute(fx.command())

        (request,) = fx.gateway.requests
        assert request.idempotency_key == f"basecamp-checkout-{fx.intent.id}"

    def test_the_session_is_stored_and_the_intent_is_left_unchanged(self, fx: Fixture) -> None:
        fx.use_case.execute(fx.command())

        stored = fx.sessions[fx.intent.id]
        assert (stored.session_id, stored.url, stored.created_at) == (
            "cs_test_a1b2c3",
            STRIPE_URL,
            NOW,
        )
        assert fx.intents[fx.intent.id] == fx.intent

    def test_a_second_call_replays_the_stored_session_without_calling_stripe(
        self, fx: Fixture
    ) -> None:
        first = fx.use_case.execute(fx.command())

        second = fx.use_case.execute(fx.command())

        assert (second.session_id, second.url) == (first.session_id, first.url)
        assert second.created is False
        assert len(fx.gateway.requests) == 1

    def test_a_concurrent_creation_returns_the_session_stored_first(self, fx: Fixture) -> None:
        rival = CheckoutSession(fx.intent.id, "cs_test_rival", STRIPE_URL + "x", NOW)
        fx.gateway.on_call = lambda: fx.sessions.__setitem__(fx.intent.id, rival)

        view = fx.use_case.execute(fx.command())

        assert (view.session_id, view.created) == ("cs_test_rival", False)
        assert fx.sessions[fx.intent.id] is rival


class TestRefusals:
    @pytest.mark.parametrize("intent_id", ["not-a-uuid", "00000000-0000-0000-0000-000000000000"])
    def test_an_unknown_or_malformed_intent_is_not_found(self, fx: Fixture, intent_id: str) -> None:
        with pytest.raises(PaymentIntentNotFoundError):
            fx.use_case.execute(fx.command(intent_id=intent_id))

        assert fx.gateway.requests == []

    @pytest.mark.parametrize(
        "status",
        [IntentStatus.SUCCEEDED, IntentStatus.FAILED, IntentStatus.CANCELED, IntentStatus.EXPIRED],
    )
    def test_an_intent_that_is_not_waiting_is_refused(
        self, fx: Fixture, status: IntentStatus
    ) -> None:
        fx.with_status(status)

        with pytest.raises(IntentNotCheckoutableError):
            fx.use_case.execute(fx.command())

        assert fx.gateway.requests == []
        assert fx.summary() == [
            (names.CHECKOUT_SESSION_REJECTED, Outcome.DENIED, f"status_{status.value}")
        ]

    def test_an_overdue_intent_is_refused(self, fx: Fixture) -> None:
        fx.intents[fx.intent.id] = replace(fx.intent, expires_at=NOW)

        with pytest.raises(IntentNotCheckoutableError):
            fx.use_case.execute(fx.command())

        assert fx.summary() == [(names.CHECKOUT_SESSION_REJECTED, Outcome.DENIED, "expired")]

    def test_an_intent_canceled_after_its_session_was_created_is_not_replayed(
        self, fx: Fixture
    ) -> None:
        fx.use_case.execute(fx.command())
        fx.with_status(IntentStatus.CANCELED)

        with pytest.raises(IntentNotCheckoutableError):
            fx.use_case.execute(fx.command())

    def test_an_intent_canceled_while_stripe_was_called_stores_nothing(self, fx: Fixture) -> None:
        fx.gateway.on_call = lambda: fx.with_status(IntentStatus.CANCELED)

        with pytest.raises(IntentNotCheckoutableError):
            fx.use_case.execute(fx.command())

        assert fx.sessions == {}

    def test_stripe_is_not_enabled_without_a_gateway(self, fx: Fixture) -> None:
        with pytest.raises(StripeNotEnabledError):
            fx.build(None).execute(fx.command())

        assert fx.summary() == [
            (names.CHECKOUT_SESSION_REJECTED, Outcome.DENIED, "stripe_not_enabled")
        ]


class TestStripeFailures:
    def test_a_stripe_failure_leaves_the_intent_untouched_and_logs_the_cause(
        self, fx: Fixture
    ) -> None:
        fx.gateway.failure = StripeCheckoutUnavailableError("stripe_server_error")

        with pytest.raises(StripeCheckoutUnavailableError):
            fx.use_case.execute(fx.command())

        assert fx.sessions == {}
        assert fx.intents[fx.intent.id] == fx.intent
        assert fx.summary() == [
            (names.CHECKOUT_SESSION_FAILED, Outcome.ERROR, "stripe_server_error")
        ]

    def test_a_retry_after_a_failure_reuses_the_same_stripe_idempotency_key(
        self, fx: Fixture
    ) -> None:
        fx.gateway.failure = StripeCheckoutUnavailableError("stripe_timeout")
        with pytest.raises(StripeCheckoutUnavailableError):
            fx.use_case.execute(fx.command())
        fx.gateway.failure = None

        fx.use_case.execute(fx.command())

        first, second = fx.gateway.requests
        assert first.idempotency_key == second.idempotency_key


class TestBusinessEvents:
    def test_a_creation_logs_the_session_without_its_url(self, fx: Fixture) -> None:
        fx.use_case.execute(fx.command())

        assert fx.summary() == [(names.CHECKOUT_SESSION_CREATED, Outcome.SUCCESS, None)]
        (event,) = fx.logger.events
        assert (event.resource_type, event.resource_id) == ("payment_intent", str(fx.intent.id))
        assert (event.actor_type, event.actor_id) == ("service", "shop")
        assert event.attributes == {
            "order_reference": "CMD-1",
            "amount_cents": 4990,
            "currency": "EUR",
            "stripe_session_id": "cs_test_a1b2c3",
        }
        assert STRIPE_URL not in repr(event)

    def test_a_replay_logs_a_replayed_event(self, fx: Fixture) -> None:
        fx.use_case.execute(fx.command())
        fx.logger.events.clear()

        fx.use_case.execute(fx.command())

        assert fx.summary() == [(names.CHECKOUT_SESSION_REPLAYED, Outcome.SUCCESS, None)]
