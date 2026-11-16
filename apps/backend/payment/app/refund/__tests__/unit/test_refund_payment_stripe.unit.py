"""Remboursement d'un paiement Stripe (WORK-90) : Stripe d'abord, écritures ensuite, une seule fois."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.__tests__.in_memory_business_event_logger import InMemoryBusinessEventLogger
from app.core.events.outcome import Outcome
from app.intent.domain.entities.payment_intent import PaymentIntent
from app.intent.domain.enums.currency import Currency
from app.intent.domain.value_objects.amount import Amount
from app.refund.__tests__.support import InMemoryStore, InMemoryUnitOfWork
from app.refund.application import refund_event_names as names
from app.refund.application.commands.refund_payment_command import RefundPaymentCommand
from app.refund.application.refund_expired_order import RefundExpiredOrderUseCase
from app.refund.application.refund_payment import RefundPaymentUseCase
from app.refund.domain.exceptions.refund_exceeds_captured_error import RefundExceedsCapturedError
from app.refund.domain.exceptions.stripe_refund_unavailable_error import (
    StripeRefundUnavailableError,
)
from app.refund.domain.interfaces.stripe_refund_gateway import StripeRefundGateway
from app.refund.domain.value_objects.created_stripe_refund import CreatedStripeRefund
from app.refund.domain.value_objects.stripe_payment_reference import StripePaymentReference
from app.refund.domain.value_objects.stripe_refund_request import StripeRefundRequest
from app.webhook.domain.entities.outbox_event import OutboxEvent
from app.webhook.domain.enums.outbox_status import OutboxStatus
from app.webhook.domain.enums.webhook_event_type import WebhookEventType
from app.webhook.domain.services.refund_event_factory import RefundEventFactory

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
STRIPE_PAYMENT = "pi_3Test1"


class FakeStripe(StripeRefundGateway):
    def __init__(self) -> None:
        self.requests: list[StripeRefundRequest] = []
        self.failure: Exception | None = None
        self.status = "succeeded"

    def create_refund(self, request: StripeRefundRequest) -> CreatedStripeRefund:
        self.requests.append(request)
        if self.failure:
            raise self.failure
        return CreatedStripeRefund("re_3Test1", self.status)


class Fixture:
    def __init__(self, stripe_paid: bool = True) -> None:
        self.store = InMemoryStore()
        self.logger = InMemoryBusinessEventLogger()
        self.stripe = FakeStripe()
        self.use_case = self.build(self.stripe)
        created = PaymentIntent.create(
            "CMD-1", Amount(10000), Currency.EUR, NOW - timedelta(minutes=5), timedelta(minutes=30)
        )
        self.intent = created.succeed()
        self.store.intents[self.intent.id] = self.intent
        if stripe_paid:
            self.store.stripe_payments[self.intent.id] = StripePaymentReference(STRIPE_PAYMENT)

    def build(self, gateway: StripeRefundGateway | None) -> RefundPaymentUseCase:
        return RefundPaymentUseCase(
            InMemoryUnitOfWork(self.store),
            lambda: NOW,
            self.logger,
            RefundEventFactory(uuid4),
            gateway,
        )

    def command(self, **overrides: object) -> RefundPaymentCommand:
        values: dict[str, object] = {
            "caller": "shop",
            "idempotency_key": "refund-0001",
            "intent_id": str(self.intent.id),
            "amount": 4000,
        }
        values.update(overrides)
        return RefundPaymentCommand(**values)  # type: ignore[arg-type]

    def stored(self) -> PaymentIntent:
        return self.store.intents[self.intent.id]


@pytest.fixture
def fx() -> Fixture:
    return Fixture()


class TestStripeRefund:
    def test_stripe_is_asked_for_the_server_side_amount_on_the_stripe_payment(
        self, fx: Fixture
    ) -> None:
        fx.use_case.execute(fx.command())

        (request,) = fx.stripe.requests
        assert (request.stripe_payment_intent_id, request.amount_cents) == (STRIPE_PAYMENT, 4000)
        assert (request.intent_id, request.origin) == (fx.intent.id, "refund")

    def test_the_ledger_the_intent_and_the_shop_event_follow_the_stripe_refund(
        self, fx: Fixture
    ) -> None:
        view = fx.use_case.execute(fx.command())

        assert (view.amount, view.amount_refunded) == (4000, 4000)
        assert fx.stored().refunded_amount == 4000
        (refund,) = fx.store.refunds.values()
        assert refund.stripe_refund_id == "re_3Test1"
        (ledger,) = fx.store.ledger
        assert len(ledger.entries) == 2
        (outbox,) = fx.store.outbox
        assert (outbox.event_type, outbox.status) == (
            WebhookEventType.REFUND_SUCCEEDED,
            OutboxStatus.PENDING,
        )

    def test_a_pending_stripe_refund_is_accepted(self, fx: Fixture) -> None:
        fx.stripe.status = "pending"

        fx.use_case.execute(fx.command())

        assert fx.stored().refunded_amount == 4000

    def test_the_stripe_key_is_derived_from_the_intent_the_caller_and_the_key(
        self, fx: Fixture
    ) -> None:
        fx.use_case.execute(fx.command())
        fx.use_case.execute(fx.command(idempotency_key="refund-0002", amount=1000))
        fx.use_case.execute(
            fx.command(caller="operator", idempotency_key="refund-0001", amount=500)
        )

        keys = [request.idempotency_key for request in fx.stripe.requests]
        assert len(set(keys)) == 3
        assert all(key.startswith(f"basecamp-refund-{fx.intent.id}-") for key in keys)
        assert all(len(key) <= 255 for key in keys)

    def test_a_replay_with_the_same_key_never_asks_stripe_again(self, fx: Fixture) -> None:
        first = fx.use_case.execute(fx.command())

        replay = fx.use_case.execute(fx.command())

        assert replay.id == first.id and replay.replayed
        assert len(fx.stripe.requests) == 1
        assert len(fx.store.ledger) == 1

    def test_a_refund_above_the_captured_amount_never_reaches_stripe(self, fx: Fixture) -> None:
        with pytest.raises(RefundExceedsCapturedError):
            fx.use_case.execute(fx.command(amount=10001))

        assert fx.stripe.requests == []

    def test_cumulated_refunds_stop_at_the_captured_amount(self, fx: Fixture) -> None:
        fx.use_case.execute(fx.command(amount=6000))

        with pytest.raises(RefundExceedsCapturedError):
            fx.use_case.execute(fx.command(idempotency_key="refund-0002", amount=5000))

        assert len(fx.stripe.requests) == 1
        assert fx.stored().refunded_amount == 6000


class TestStripeFailure:
    @pytest.mark.parametrize(
        "reason", ["stripe_timeout", "stripe_unreachable", "stripe_server_error", "stripe_rejected"]
    )
    def test_nothing_changes_and_the_error_is_logged_with_a_fixed_reason(
        self, fx: Fixture, reason: str
    ) -> None:
        fx.stripe.failure = StripeRefundUnavailableError(reason)

        with pytest.raises(StripeRefundUnavailableError):
            fx.use_case.execute(fx.command())

        assert fx.stored() == fx.intent
        assert fx.store.refunds == {} and fx.store.ledger == [] and fx.store.outbox == []
        (logged,) = fx.logger.events
        assert (logged.event, logged.outcome, logged.reason) == (
            names.REFUND_STRIPE_FAILED,
            Outcome.ERROR,
            reason,
        )

    def test_a_refund_stripe_did_not_accept_changes_nothing(self, fx: Fixture) -> None:
        fx.stripe.status = "failed"

        with pytest.raises(StripeRefundUnavailableError):
            fx.use_case.execute(fx.command())

        assert fx.stored() == fx.intent and fx.store.ledger == []
        assert fx.logger.events[0].reason == "stripe_refund_not_accepted"

    def test_the_replayed_request_reuses_the_same_stripe_key_and_then_succeeds(
        self, fx: Fixture
    ) -> None:
        fx.stripe.failure = StripeRefundUnavailableError("stripe_timeout")
        with pytest.raises(StripeRefundUnavailableError):
            fx.use_case.execute(fx.command())
        fx.stripe.failure = None

        view = fx.use_case.execute(fx.command())

        first, second = fx.stripe.requests
        assert first.idempotency_key == second.idempotency_key
        assert view.amount_refunded == 4000

    def test_without_a_stripe_gateway_a_stripe_payment_cannot_be_refunded(
        self, fx: Fixture
    ) -> None:
        with pytest.raises(StripeRefundUnavailableError):
            fx.build(None).execute(fx.command())

        assert fx.stored() == fx.intent
        assert fx.logger.events[0].reason == "stripe_not_enabled"

    def test_a_stripe_payment_without_its_reference_cannot_be_refunded(self, fx: Fixture) -> None:
        fx.store.stripe_payments[fx.intent.id] = StripePaymentReference(None)

        with pytest.raises(StripeRefundUnavailableError):
            fx.use_case.execute(fx.command())

        assert fx.stripe.requests == []
        assert fx.logger.events[0].reason == "stripe_payment_reference_missing"


class TestInternalPaymentsAreUntouched:
    def test_a_payment_not_made_through_stripe_never_calls_stripe(self) -> None:
        fx = Fixture(stripe_paid=False)

        view = fx.use_case.execute(fx.command())

        assert view.amount_refunded == 4000
        assert fx.stripe.requests == []
        (refund,) = fx.store.refunds.values()
        assert refund.stripe_refund_id is None


class TestAutomaticRefundOfAnExpiredOrder:
    def test_the_worker_refund_goes_through_stripe_with_a_key_derived_from_the_event(
        self, fx: Fixture
    ) -> None:
        refunder = RefundExpiredOrderUseCase(fx.use_case)
        event = OutboxEvent.pending(
            uuid4(),
            WebhookEventType.PAYMENT_SUCCEEDED,
            fx.intent.id,
            '{"data":{"amount":10000}}',
            NOW,
        )

        refunder.refund_in_full(event)
        refunder.refund_in_full(event)

        assert len(fx.stripe.requests) == 1
        assert fx.stripe.requests[0].amount_cents == 10000
        assert fx.stored().refunded_amount == 10000
        assert len(fx.store.ledger) == 1
