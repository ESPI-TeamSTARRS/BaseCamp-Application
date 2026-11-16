"""Confirmation par événement Stripe (WORK-89) : cas d'usage avec une base en mémoire."""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from app.confirmation.application import confirmation_event_names as confirmation_names
from app.core.events.outcome import Outcome
from app.intent.domain.enums.intent_status import IntentStatus
from app.stripe_webhook.__tests__.support import NOW, Fixture
from app.stripe_webhook.application import stripe_webhook_event_names as names
from app.stripe_webhook.domain.value_objects.stripe_event import StripeEvent
from app.webhook.application import webhook_event_names as webhook_names
from app.webhook.domain.enums.webhook_event_type import WebhookEventType


@pytest.fixture
def fx() -> Fixture:
    return Fixture()


class TestPaidSession:
    def test_the_intent_succeeds_the_ledger_is_written_and_the_shop_is_notified(
        self, fx: Fixture
    ) -> None:
        fx.use_case.execute(fx.completed())

        assert fx.current.status is IntentStatus.SUCCEEDED
        (ledger,) = fx.store.ledgers
        assert len(ledger.entries) == 2
        assert sum(e.amount.cents for e in ledger.entries) == 2 * 4990
        (outbox,) = fx.store.outbox
        assert outbox.event_type is WebhookEventType.PAYMENT_SUCCEEDED
        assert outbox.intent_id == fx.intent.id

    def test_the_event_is_recorded_with_its_outcome(self, fx: Fixture) -> None:
        fx.use_case.execute(fx.completed())

        recorded = fx.store.events["evt_1"]
        assert (recorded.event_type, recorded.outcome, recorded.intent_id) == (
            "checkout.session.completed",
            "confirmed",
            fx.intent.id,
        )

    def test_a_payment_captured_after_the_deadline_is_still_confirmed(self, fx: Fixture) -> None:
        fx.store.intents[fx.intent.id] = replace(fx.intent, expires_at=NOW - timedelta(seconds=1))

        fx.use_case.execute(fx.completed())

        assert fx.current.status is IntentStatus.SUCCEEDED

    def test_a_replayed_event_has_no_second_effect(self, fx: Fixture) -> None:
        fx.use_case.execute(fx.completed())
        fx.logger.events.clear()

        fx.use_case.execute(fx.completed())

        assert len(fx.store.ledgers) == 1 and len(fx.store.outbox) == 1
        assert fx.summary() == [(names.STRIPE_EVENT_REPLAYED, Outcome.SUCCESS, None)]

    def test_a_concurrent_duplicate_is_a_replay_too(self, fx: Fixture) -> None:
        fx.store.race_on_record = True

        fx.use_case.execute(fx.completed())

        assert fx.summary() == [(names.STRIPE_EVENT_REPLAYED, Outcome.SUCCESS, None)]

    def test_a_second_event_for_the_same_intent_has_no_second_effect(self, fx: Fixture) -> None:
        fx.use_case.execute(fx.completed("evt_1"))

        fx.use_case.execute(fx.completed("evt_other"))

        assert len(fx.store.ledgers) == 1 and len(fx.store.outbox) == 1
        assert fx.store.events["evt_other"].outcome == "ignored"


class TestDiscrepancies:
    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            ({"amount_total": 100}, "amount_mismatch"),
            ({"amount_total": "4990"}, "amount_mismatch"),
            ({"amount_total": True}, "amount_mismatch"),
            ({"amount_total": None}, "amount_mismatch"),
            ({"currency": "usd"}, "currency_mismatch"),
            ({"currency": None}, "currency_mismatch"),
            ({"id": "cs_test_autre"}, "session_mismatch"),
            ({"payment_status": "unpaid"}, "payment_not_paid"),
            ({"payment_status": None}, "payment_not_paid"),
        ],
    )
    def test_nothing_is_confirmed_and_an_alert_is_raised(
        self, fx: Fixture, overrides: dict[str, Any], reason: str
    ) -> None:
        fx.use_case.execute(fx.completed(**overrides))

        assert fx.current.status is IntentStatus.REQUIRES_CONFIRMATION
        assert fx.store.ledgers == [] and fx.store.outbox == []
        (logged,) = fx.logger.events
        assert (logged.event, logged.reason) == (names.STRIPE_EVENT_IGNORED, reason)
        assert fx.store.events["evt_1"].outcome == "ignored"

    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            ({"amount_total": 100}, Outcome.ERROR),
            ({"currency": "usd"}, Outcome.ERROR),
            ({"id": "cs_test_autre"}, Outcome.ERROR),
            ({"payment_status": "unpaid"}, Outcome.DENIED),
        ],
    )
    def test_money_discrepancies_are_errors_and_the_rest_are_denials(
        self, fx: Fixture, overrides: dict[str, Any], expected: Outcome
    ) -> None:
        fx.use_case.execute(fx.completed(**overrides))

        assert fx.logger.events[0].outcome is expected

    def test_a_session_for_an_intent_without_our_session_is_a_mismatch(self, fx: Fixture) -> None:
        fx.store.sessions.clear()

        fx.use_case.execute(fx.completed())

        assert fx.current.status is IntentStatus.REQUIRES_CONFIRMATION
        assert fx.logger.events[0].reason == "session_mismatch"

    @pytest.mark.parametrize(
        "status", [IntentStatus.SUCCEEDED, IntentStatus.FAILED, IntentStatus.CANCELED]
    )
    def test_a_payment_on_an_intent_that_is_no_longer_pending_is_an_alert(
        self, fx: Fixture, status: IntentStatus
    ) -> None:
        fx.with_status(status)

        fx.use_case.execute(fx.completed())

        assert fx.current.status is status
        assert fx.store.ledgers == [] and fx.store.outbox == []
        (logged,) = fx.logger.events
        assert (logged.reason, logged.outcome) == ("intent_not_pending", Outcome.ERROR)

    def test_an_unknown_intent_is_ignored_and_not_recorded(self, fx: Fixture) -> None:
        fx.store.intents.clear()

        fx.use_case.execute(fx.completed())

        assert fx.logger.events[0].reason == "intent_not_found"
        assert fx.store.ledgers == [] and fx.store.events["evt_1"].outcome == "ignored"


class TestFailedPayment:
    def test_the_intent_fails_without_any_ledger_entry(self, fx: Fixture) -> None:
        fx.use_case.execute(fx.failed())

        assert fx.current.status is IntentStatus.FAILED
        assert fx.store.ledgers == []
        (outbox,) = fx.store.outbox
        assert outbox.event_type is WebhookEventType.PAYMENT_FAILED

    def test_a_failure_on_a_settled_intent_is_ignored(self, fx: Fixture) -> None:
        fx.use_case.execute(fx.completed())

        fx.use_case.execute(fx.failed())

        assert fx.current.status is IntentStatus.SUCCEEDED
        assert len(fx.store.outbox) == 1


class TestIgnoredEvents:
    def test_an_unsupported_type_is_ignored_without_reading_any_state(self, fx: Fixture) -> None:
        event = StripeEvent("evt_9", "customer.created", {"id": "cus_1"})

        fx.use_case.execute(event)

        assert fx.store.reads == 0 and fx.store.events == {}
        assert fx.summary() == [(names.STRIPE_EVENT_IGNORED, Outcome.DENIED, "unsupported_type")]

    @pytest.mark.parametrize(
        "metadata", [None, {}, {"payment_intent_id": 12}, {"payment_intent_id": "pas-un-uuid"}]
    )
    def test_an_event_without_a_usable_intent_reference_is_ignored(
        self, fx: Fixture, metadata: Any
    ) -> None:
        fx.use_case.execute(fx.completed(metadata=metadata))

        assert fx.store.reads == 0 and fx.store.events == {}
        assert fx.logger.events[0].reason == "intent_reference_missing"


class TestBusinessEvents:
    def test_a_confirmation_logs_the_payment_the_ledger_and_the_queued_event(
        self, fx: Fixture
    ) -> None:
        fx.use_case.execute(fx.completed())

        assert [e.event for e in fx.logger.events] == [
            confirmation_names.PAYMENT_CONFIRMED,
            confirmation_names.LEDGER_ENTRIES_WRITTEN,
            webhook_names.WEBHOOK_EVENT_QUEUED,
            names.STRIPE_EVENT_PROCESSED,
        ]
        confirmed = fx.logger.events[0]
        assert (confirmed.actor_type, confirmed.actor_id) == ("service", "stripe")
        assert confirmed.resource_id == str(fx.intent.id)
        assert confirmed.attributes["amount_cents"] == 4990

    def test_a_failure_logs_a_declined_payment_and_no_ledger(self, fx: Fixture) -> None:
        fx.use_case.execute(fx.failed())

        assert [(e.event, e.outcome) for e in fx.logger.events] == [
            (confirmation_names.PAYMENT_DECLINED, Outcome.DENIED),
            (webhook_names.WEBHOOK_EVENT_QUEUED, Outcome.SUCCESS),
            (names.STRIPE_EVENT_PROCESSED, Outcome.SUCCESS),
        ]
