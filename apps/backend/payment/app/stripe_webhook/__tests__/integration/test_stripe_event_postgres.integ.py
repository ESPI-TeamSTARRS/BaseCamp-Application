"""Confirmation par événement Stripe sur Postgres (WORK-89) : idempotence et concurrence réelles.

Exécutés si PAYMENT_TEST_DATABASE_URL pointe vers une base Postgres JETABLE.
"""

import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from alembic import command
from app.core.__tests__.in_memory_business_event_logger import InMemoryBusinessEventLogger
from app.core.__tests__.postgres_support import (
    add_intent,
    alembic_config,
    migrated_engine,
    requires_postgres,
)
from app.intent.domain.entities.payment_intent import PaymentIntent
from app.intent.infrastructure.models.payment_intent_model import PaymentIntentModel
from app.ledger.infrastructure.models.ledger_entry_model import LedgerEntryModel
from app.stripe_webhook.application.process_stripe_event import ProcessStripeEventUseCase
from app.stripe_webhook.domain.value_objects.stripe_event import StripeEvent
from app.stripe_webhook.infrastructure.models.stripe_event_model import StripeEventModel
from app.stripe_webhook.infrastructure.repositories.sqlalchemy_stripe_event_unit_of_work import (
    SqlAlchemyStripeEventUnitOfWork,
)
from app.webhook.infrastructure.models.outbox_event_model import OutboxEventModel

pytestmark = requires_postgres

SESSION_ID = "cs_test_postgres1"
THREADS = 8


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    command.downgrade(alembic_config(monkeypatch), "base")
    yield from migrated_engine(monkeypatch)


def _paid_session(engine: Engine) -> PaymentIntent:
    intent = add_intent(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO stripe_checkout_sessions (intent_id, session_id, url, created_at) "
                "VALUES (:intent, :session, 'https://checkout.stripe.com/c/pay/x', now())"
            ),
            {"intent": intent.id, "session": SESSION_ID},
        )
    return intent


def _completed(intent: PaymentIntent, event_id: str) -> StripeEvent:
    return StripeEvent(
        event_id,
        "checkout.session.completed",
        {
            "id": SESSION_ID,
            "payment_status": "paid",
            "amount_total": intent.amount.cents,
            "currency": "eur",
            "metadata": {"payment_intent_id": str(intent.id)},
        },
    )


def _run_concurrently(engine: Engine, events: list[StripeEvent]) -> list[BaseException]:
    use_case = ProcessStripeEventUseCase(
        SqlAlchemyStripeEventUnitOfWork(engine),
        lambda: datetime.now(UTC),
        InMemoryBusinessEventLogger(),
    )
    barrier = threading.Barrier(len(events))
    errors: list[BaseException] = []

    def work(event: StripeEvent) -> None:
        barrier.wait()
        try:
            use_case.execute(event)
        except BaseException as error:  # remonté au test
            errors.append(error)

    threads = [threading.Thread(target=work, args=(event,)) for event in events]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return errors


def _count(engine: Engine, model: Any) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def _status(engine: Engine, intent: PaymentIntent) -> str:
    with Session(engine) as session:
        model = session.get(PaymentIntentModel, intent.id)
        assert model
        return model.status


class TestIdempotenceOnPostgres:
    def test_the_same_event_delivered_concurrently_has_one_effect(self, engine: Engine) -> None:
        intent = _paid_session(engine)

        errors = _run_concurrently(engine, [_completed(intent, "evt_same")] * THREADS)

        assert errors == []
        assert _status(engine, intent) == "succeeded"
        assert _count(engine, LedgerEntryModel) == 2
        assert _count(engine, OutboxEventModel) == 1
        assert _count(engine, StripeEventModel) == 1

    def test_different_events_for_the_same_intent_confirm_it_once(self, engine: Engine) -> None:
        intent = _paid_session(engine)
        events = [_completed(intent, f"evt_{index}") for index in range(THREADS)]

        errors = _run_concurrently(engine, events)

        assert errors == []
        assert _status(engine, intent) == "succeeded"
        assert _count(engine, LedgerEntryModel) == 2
        assert _count(engine, OutboxEventModel) == 1
        assert _count(engine, StripeEventModel) == THREADS

    def test_a_sequential_replay_changes_nothing(self, engine: Engine) -> None:
        intent = _paid_session(engine)

        _run_concurrently(engine, [_completed(intent, "evt_once")])
        _run_concurrently(engine, [_completed(intent, "evt_once")])

        assert _count(engine, LedgerEntryModel) == 2
        assert _count(engine, OutboxEventModel) == 1


class TestMigrationIsAdditive:
    def test_the_event_table_is_added_and_the_database_refuses_a_duplicate_id(
        self, engine: Engine
    ) -> None:
        intent = _paid_session(engine)
        insert = text(
            "INSERT INTO stripe_events (event_id, event_type, outcome, intent_id, processed_at) "
            "VALUES ('evt_dup', 'checkout.session.completed', 'ignored', :intent, now())"
        )
        with engine.begin() as connection:
            connection.execute(insert, {"intent": intent.id})

        with pytest.raises(Exception, match="stripe_events"), engine.begin() as connection:
            connection.execute(insert, {"intent": intent.id})
