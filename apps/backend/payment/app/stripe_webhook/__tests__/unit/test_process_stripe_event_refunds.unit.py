"""Remboursements Stripe (WORK-90) : référence de paiement, paiement tardif, rapprochement des événements."""

from typing import Any

import pytest

from app.core.events.outcome import Outcome
from app.intent.domain.enums.intent_status import IntentStatus
from app.refund.domain.exceptions.stripe_refund_unavailable_error import (
    StripeRefundUnavailableError,
)
from app.refund.domain.interfaces.stripe_refund_gateway import StripeRefundGateway
from app.refund.domain.value_objects.created_stripe_refund import CreatedStripeRefund
from app.refund.domain.value_objects.stripe_refund_request import StripeRefundRequest
from app.stripe_webhook.__tests__.support import NOW, Fixture, InMemoryUnitOfWork
from app.stripe_webhook.application import stripe_webhook_event_names as names
from app.stripe_webhook.application.process_stripe_event import ProcessStripeEventUseCase
from app.stripe_webhook.domain.value_objects.stripe_event import StripeEvent


class FakeRefunds(StripeRefundGateway):
    def __init__(self) -> None:
        self.requests: list[StripeRefundRequest] = []
        self.failure: Exception | None = None
        self.status = "succeeded"

    def create_refund(self, request: StripeRefundRequest) -> CreatedStripeRefund:
        self.requests.append(request)
        if self.failure:
            raise self.failure
        return CreatedStripeRefund("re_late", self.status)


@pytest.fixture
def fx() -> Fixture:
    return Fixture()


def _with_refunds(fx: Fixture) -> FakeRefunds:
    gateway = FakeRefunds()
    fx.use_case = ProcessStripeEventUseCase(
        InMemoryUnitOfWork(fx.store), lambda: NOW, fx.logger, gateway
    )
    return gateway


class TestPaymentReference:
    def test_the_stripe_payment_is_kept_when_the_intent_is_confirmed(self, fx: Fixture) -> None:
        fx.use_case.execute(fx.completed(payment_intent="pi_3Abc"))

        assert fx.store.references == {fx.intent.id: "pi_3Abc"}

    @pytest.mark.parametrize("value", [None, 12, "pas_un_paiement", ""])
    def test_a_missing_or_malformed_reference_still_confirms_without_keeping_it(
        self, fx: Fixture, value: Any
    ) -> None:
        fx.use_case.execute(fx.completed(payment_intent=value))

        assert fx.current.status is IntentStatus.SUCCEEDED
        assert fx.store.references == {}


class TestLatePayment:
    @pytest.mark.parametrize(
        "status", [IntentStatus.FAILED, IntentStatus.CANCELED, IntentStatus.EXPIRED]
    )
    def test_a_valid_payment_on_a_dead_intent_is_refunded_in_full_at_stripe(
        self, fx: Fixture, status: IntentStatus
    ) -> None:
        gateway = _with_refunds(fx)
        fx.with_status(status)

        fx.use_case.execute(fx.completed(payment_intent="pi_3Late"))

        (request,) = gateway.requests
        assert (request.stripe_payment_intent_id, request.amount_cents) == ("pi_3Late", 4990)
        assert (request.origin, request.idempotency_key) == (
            "late_payment",
            f"basecamp-late-refund-{fx.intent.id}",
        )
        assert fx.current.status is status
        assert fx.store.ledgers == [] and fx.store.outbox == []
        assert fx.store.events["evt_1"].outcome == "refunded_late"
        assert fx.summary() == [(names.STRIPE_LATE_PAYMENT_REFUNDED, Outcome.SUCCESS, None)]

    def test_the_second_card_scenario_end_to_end(self, fx: Fixture) -> None:
        gateway = _with_refunds(fx)
        fx.use_case.execute(fx.failed())

        fx.use_case.execute(fx.completed("evt_late", payment_intent="pi_3Late"))

        assert fx.current.status is IntentStatus.FAILED
        assert len(gateway.requests) == 1
        assert fx.store.ledgers == []

    def test_a_replayed_late_event_never_refunds_twice(self, fx: Fixture) -> None:
        gateway = _with_refunds(fx)
        fx.with_status(IntentStatus.FAILED)
        fx.use_case.execute(fx.completed(payment_intent="pi_3Late"))

        fx.use_case.execute(fx.completed(payment_intent="pi_3Late"))

        assert len(gateway.requests) == 1

    def test_a_stripe_failure_cancels_everything_and_lets_stripe_retry_the_event(
        self, fx: Fixture
    ) -> None:
        gateway = _with_refunds(fx)
        gateway.failure = StripeRefundUnavailableError("stripe_timeout")
        fx.with_status(IntentStatus.FAILED)

        with pytest.raises(StripeRefundUnavailableError):
            fx.use_case.execute(fx.completed(payment_intent="pi_3Late"))

        assert fx.summary() == [(names.STRIPE_LATE_REFUND_FAILED, Outcome.ERROR, "stripe_timeout")]

    def test_a_refund_stripe_did_not_accept_is_a_failure_too(self, fx: Fixture) -> None:
        gateway = _with_refunds(fx)
        gateway.status = "failed"
        fx.with_status(IntentStatus.FAILED)

        with pytest.raises(StripeRefundUnavailableError):
            fx.use_case.execute(fx.completed(payment_intent="pi_3Late"))

    def test_a_payment_that_does_not_match_the_intent_is_never_refunded_automatically(
        self, fx: Fixture
    ) -> None:
        gateway = _with_refunds(fx)
        fx.with_status(IntentStatus.FAILED)

        fx.use_case.execute(fx.completed(payment_intent="pi_3Late", amount_total=1))

        assert gateway.requests == []
        assert fx.logger.events[0].reason == "amount_mismatch"

    def test_without_a_reference_the_alert_stays(self, fx: Fixture) -> None:
        gateway = _with_refunds(fx)
        fx.with_status(IntentStatus.FAILED)

        fx.use_case.execute(fx.completed())

        assert gateway.requests == []
        assert fx.logger.events[0].reason == "intent_not_pending"

    def test_a_succeeded_intent_is_a_duplicate_not_a_second_payment(self, fx: Fixture) -> None:
        gateway = _with_refunds(fx)
        fx.use_case.execute(fx.completed("evt_1", payment_intent="pi_3Ok"))

        fx.use_case.execute(fx.completed("evt_again", payment_intent="pi_3Ok"))

        assert gateway.requests == []


def _refund_event(fx: Fixture, event_type: str = "refund.updated", **overrides: Any) -> StripeEvent:
    obj: dict[str, Any] = {
        "id": "re_3Known",
        "status": "succeeded",
        "metadata": {"payment_intent_id": str(fx.intent.id)},
    }
    obj.update(overrides)
    return StripeEvent("evt_refund", event_type, obj)


class TestRefundEvents:
    def test_the_event_of_a_refund_we_issued_is_confirmed_without_any_ledger_write(
        self, fx: Fixture
    ) -> None:
        fx.store.stripe_refunds.add("re_3Known")

        fx.use_case.execute(_refund_event(fx))

        assert fx.store.ledgers == [] and fx.store.outbox == []
        assert fx.store.events["evt_refund"].outcome == "refund_confirmed"
        assert fx.summary() == [(names.STRIPE_REFUND_CONFIRMED, Outcome.SUCCESS, None)]

    @pytest.mark.parametrize("event_type", ["refund.created", "refund.updated"])
    def test_creation_and_update_events_are_handled(self, fx: Fixture, event_type: str) -> None:
        fx.store.stripe_refunds.add("re_3Known")

        fx.use_case.execute(_refund_event(fx, event_type))

        assert fx.logger.events[0].event == names.STRIPE_REFUND_CONFIRMED

    @pytest.mark.parametrize(
        ("event_type", "status"),
        [("refund.updated", "failed"), ("refund.updated", "canceled"), ("refund.failed", "failed")],
    )
    def test_a_refund_that_failed_at_stripe_raises_an_alert_and_writes_nothing(
        self, fx: Fixture, event_type: str, status: str
    ) -> None:
        fx.store.stripe_refunds.add("re_3Known")

        fx.use_case.execute(_refund_event(fx, event_type, status=status))

        assert fx.store.ledgers == [] and fx.store.outbox == []
        (logged,) = fx.logger.events
        assert (logged.event, logged.outcome, logged.reason) == (
            names.STRIPE_REFUND_FAILED,
            Outcome.ERROR,
            "refund_failed_at_stripe",
        )

    def test_a_refund_we_did_not_issue_is_ignored(self, fx: Fixture) -> None:
        fx.use_case.execute(_refund_event(fx))

        assert fx.logger.events[0].reason == "refund_unknown"
        assert fx.logger.events[0].outcome is Outcome.DENIED

    def test_the_automatic_refund_of_a_late_payment_is_recognised_by_its_origin(
        self, fx: Fixture
    ) -> None:
        metadata = {"payment_intent_id": str(fx.intent.id), "basecamp_origin": "late_payment"}

        fx.use_case.execute(_refund_event(fx, metadata=metadata))

        assert fx.logger.events[0].event == names.STRIPE_REFUND_CONFIRMED

    def test_a_replayed_refund_event_has_no_second_effect(self, fx: Fixture) -> None:
        fx.store.stripe_refunds.add("re_3Known")
        fx.use_case.execute(_refund_event(fx))
        fx.logger.events.clear()

        fx.use_case.execute(_refund_event(fx))

        assert fx.summary() == [(names.STRIPE_EVENT_REPLAYED, Outcome.SUCCESS, None)]
