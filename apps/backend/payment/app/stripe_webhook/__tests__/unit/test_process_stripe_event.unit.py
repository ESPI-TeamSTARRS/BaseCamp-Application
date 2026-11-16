"""Confirmation par événement Stripe (WORK-89) : cas d'usage avec une base en mémoire."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from app.confirmation.application import confirmation_event_names as confirmation_names
from app.core.__tests__.in_memory_business_event_logger import InMemoryBusinessEventLogger
from app.core.events.outcome import Outcome
from app.intent.domain.entities.payment_intent import PaymentIntent
from app.intent.domain.enums.currency import Currency
from app.intent.domain.enums.intent_status import IntentStatus
from app.intent.domain.value_objects.amount import Amount
from app.ledger.domain.entities.ledger_transaction import LedgerTransaction
from app.stripe_webhook.application import stripe_webhook_event_names as names
from app.stripe_webhook.application.process_stripe_event import ProcessStripeEventUseCase
from app.stripe_webhook.domain.entities.processed_stripe_event import ProcessedStripeEvent
from app.stripe_webhook.domain.errors.duplicate_stripe_event_error import (
    DuplicateStripeEventError,
)
from app.stripe_webhook.domain.interfaces.stripe_event_session import StripeEventSession
from app.stripe_webhook.domain.interfaces.stripe_event_unit_of_work import StripeEventUnitOfWork
from app.stripe_webhook.domain.value_objects.stripe_event import StripeEvent
from app.webhook.application import webhook_event_names as webhook_names
from app.webhook.domain.entities.outbox_event import OutboxEvent
from app.webhook.domain.enums.webhook_event_type import WebhookEventType

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
SESSION_ID = "cs_test_a1b2c3"


@dataclass
class Store:
    intents: dict[UUID, PaymentIntent]
    sessions: dict[UUID, str] = field(default_factory=dict)
    events: dict[str, ProcessedStripeEvent] = field(default_factory=dict)
    ledgers: list[LedgerTransaction] = field(default_factory=list)
    outbox: list[OutboxEvent] = field(default_factory=list)
    reads: int = 0
    race_on_record: bool = False


class InMemorySession(StripeEventSession):
    def __init__(self, store: Store) -> None:
        self._store = store

    def is_processed(self, event_id: str) -> bool:
        return event_id in self._store.events

    def lock_intent(self, intent_id: UUID) -> PaymentIntent | None:
        self._store.reads += 1
        return self._store.intents.get(intent_id)

    def get_checkout_session_id(self, intent_id: UUID) -> str | None:
        return self._store.sessions.get(intent_id)

    def record_event(self, event: ProcessedStripeEvent) -> None:
        if self._store.race_on_record or event.event_id in self._store.events:
            raise DuplicateStripeEventError
        self._store.events[event.event_id] = event

    def save_settlement(
        self, intent: PaymentIntent, ledger: LedgerTransaction | None, outbox_event: OutboxEvent
    ) -> None:
        self._store.intents[intent.id] = intent
        if ledger:
            self._store.ledgers.append(ledger)
        self._store.outbox.append(outbox_event)


class InMemoryUnitOfWork(StripeEventUnitOfWork):
    def __init__(self, store: Store) -> None:
        self._store = store

    @contextmanager
    def begin(self) -> Iterator[StripeEventSession]:
        yield InMemorySession(self._store)


class Fixture:
    def __init__(self) -> None:
        self.intent = PaymentIntent.create(
            "CMD-1", Amount(4990), Currency.EUR, NOW - timedelta(minutes=1), timedelta(minutes=30)
        )
        self.store = Store({self.intent.id: self.intent}, {self.intent.id: SESSION_ID})
        self.logger = InMemoryBusinessEventLogger()
        self.use_case = ProcessStripeEventUseCase(
            InMemoryUnitOfWork(self.store), lambda: NOW, self.logger
        )

    def completed(self, event_id: str = "evt_1", **overrides: Any) -> StripeEvent:
        obj: dict[str, Any] = {
            "id": SESSION_ID,
            "payment_status": "paid",
            "amount_total": 4990,
            "currency": "eur",
            "metadata": {"payment_intent_id": str(self.intent.id)},
        }
        obj.update(overrides)
        return StripeEvent(event_id, "checkout.session.completed", obj)

    def failed(self, event_id: str = "evt_2") -> StripeEvent:
        obj = {"id": "pi_test_1", "metadata": {"payment_intent_id": str(self.intent.id)}}
        return StripeEvent(event_id, "payment_intent.payment_failed", obj)

    def with_status(self, status: IntentStatus) -> None:
        self.intent = replace(self.intent, status=status)
        self.store.intents[self.intent.id] = self.intent

    @property
    def current(self) -> PaymentIntent:
        return self.store.intents[self.intent.id]

    def summary(self) -> list[tuple[str, Outcome, str | None]]:
        return [(e.event, e.outcome, e.reason) for e in self.logger.events]


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
