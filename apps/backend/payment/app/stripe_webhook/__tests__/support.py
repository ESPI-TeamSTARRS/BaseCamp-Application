"""Doublures en mémoire du traitement des événements Stripe, partagées par les tests (pas un test)."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.core.__tests__.in_memory_business_event_logger import InMemoryBusinessEventLogger
from app.core.events.outcome import Outcome
from app.intent.domain.entities.payment_intent import PaymentIntent
from app.intent.domain.enums.currency import Currency
from app.intent.domain.enums.intent_status import IntentStatus
from app.intent.domain.value_objects.amount import Amount
from app.ledger.domain.entities.ledger_transaction import LedgerTransaction
from app.stripe_webhook.application.process_stripe_event import ProcessStripeEventUseCase
from app.stripe_webhook.domain.entities.processed_stripe_event import ProcessedStripeEvent
from app.stripe_webhook.domain.errors.duplicate_stripe_event_error import (
    DuplicateStripeEventError,
)
from app.stripe_webhook.domain.interfaces.stripe_event_session import StripeEventSession
from app.stripe_webhook.domain.interfaces.stripe_event_unit_of_work import StripeEventUnitOfWork
from app.stripe_webhook.domain.value_objects.stripe_event import StripeEvent
from app.webhook.domain.entities.outbox_event import OutboxEvent

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
    references: dict[UUID, str] = field(default_factory=dict)
    stripe_refunds: set[str] = field(default_factory=set)


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

    def save_payment_reference(self, intent_id: UUID, stripe_payment_intent_id: str) -> None:
        self._store.references[intent_id] = stripe_payment_intent_id

    def has_stripe_refund(self, stripe_refund_id: str) -> bool:
        return stripe_refund_id in self._store.stripe_refunds

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
