"""Remboursements Stripe sur Postgres (WORK-90) : cumul sous concurrence, un seul appel par remboursement
accepté, grand livre cohérent (ledger-check).

Exécutés si PAYMENT_TEST_DATABASE_URL pointe vers une base Postgres JETABLE.
"""

import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

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
from app.ledgercheck.application.audit_ledger import AuditLedgerUseCase
from app.ledgercheck.application.check_ledger_consistency import CheckLedgerConsistencyUseCase
from app.ledgercheck.infrastructure.readers.sqlalchemy_ledger_reader import SqlAlchemyLedgerReader
from app.refund.application.commands.refund_payment_command import RefundPaymentCommand
from app.refund.application.refund_payment import RefundPaymentUseCase
from app.refund.domain.exceptions.refund_exceeds_captured_error import RefundExceedsCapturedError
from app.refund.domain.interfaces.stripe_refund_gateway import StripeRefundGateway
from app.refund.domain.value_objects.created_stripe_refund import CreatedStripeRefund
from app.refund.domain.value_objects.stripe_refund_request import StripeRefundRequest
from app.refund.infrastructure.models.refund_model import RefundModel
from app.refund.infrastructure.repositories.sqlalchemy_refund_unit_of_work import (
    SqlAlchemyRefundUnitOfWork,
)
from app.stripe_webhook.application.process_stripe_event import ProcessStripeEventUseCase
from app.stripe_webhook.domain.value_objects.stripe_event import StripeEvent
from app.stripe_webhook.infrastructure.repositories.sqlalchemy_stripe_event_unit_of_work import (
    SqlAlchemyStripeEventUnitOfWork,
)

pytestmark = requires_postgres

SESSION_ID = "cs_test_pgrefund"
STRIPE_PAYMENT = "pi_3PgRefund"


class CountingStripe(StripeRefundGateway):
    def __init__(self, pause: float = 0.0) -> None:
        self.calls = 0
        self._pause = pause
        self._lock = threading.Lock()

    def create_refund(self, request: StripeRefundRequest) -> CreatedStripeRefund:
        time.sleep(self._pause)
        with self._lock:
            self.calls += 1
            return CreatedStripeRefund(f"re_pg{self.calls}", "succeeded")


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    command.downgrade(alembic_config(monkeypatch), "base")
    yield from migrated_engine(monkeypatch)


def _paid_through_stripe(engine: Engine) -> PaymentIntent:
    intent = add_intent(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO stripe_checkout_sessions (intent_id, session_id, url, created_at) "
                "VALUES (:intent, :session, 'https://checkout.stripe.com/c/pay/x', now())"
            ),
            {"intent": intent.id, "session": SESSION_ID},
        )
    ProcessStripeEventUseCase(
        SqlAlchemyStripeEventUnitOfWork(engine),
        lambda: datetime.now(UTC),
        InMemoryBusinessEventLogger(),
    ).execute(
        StripeEvent(
            "evt_pg_pay",
            "checkout.session.completed",
            {
                "id": SESSION_ID,
                "payment_status": "paid",
                "amount_total": intent.amount.cents,
                "currency": "eur",
                "payment_intent": STRIPE_PAYMENT,
                "metadata": {"payment_intent_id": str(intent.id)},
            },
        )
    )
    return intent


def _use_case(engine: Engine, stripe: StripeRefundGateway) -> RefundPaymentUseCase:
    return RefundPaymentUseCase(
        SqlAlchemyRefundUnitOfWork(engine),
        lambda: datetime.now(UTC),
        InMemoryBusinessEventLogger(),
        stripe_gateway=stripe,
    )


def _command(intent: PaymentIntent, key: str, amount: int) -> RefundPaymentCommand:
    return RefundPaymentCommand("shop", key, str(intent.id), amount)


def _run(
    engine: Engine,
    intent: PaymentIntent,
    stripe: StripeRefundGateway,
    attempts: list[tuple[str, int]],
) -> list[str]:
    use_case = _use_case(engine, stripe)
    barrier = threading.Barrier(len(attempts))
    outcomes: list[str] = []

    def attempt(key: str, amount: int) -> None:
        barrier.wait()
        try:
            use_case.execute(_command(intent, key, amount))
            outcomes.append("ok")
        except RefundExceedsCapturedError:
            outcomes.append("conflict")

    threads = [threading.Thread(target=attempt, args=item) for item in attempts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return sorted(outcomes)


def _refunded(engine: Engine, intent_id: UUID) -> int:
    with Session(engine) as session:
        model = session.get(PaymentIntentModel, intent_id)
        assert model
        return int(model.refunded_amount)


def _coherent(engine: Engine) -> bool:
    report = CheckLedgerConsistencyUseCase(
        AuditLedgerUseCase(SqlAlchemyLedgerReader(engine)),
        InMemoryBusinessEventLogger(),
        time.perf_counter,
    ).execute()
    return report.is_consistent


class TestStripePaymentOnPostgres:
    def test_the_stripe_confirmation_keeps_the_payment_reference_for_the_refund(
        self, engine: Engine
    ) -> None:
        intent = _paid_through_stripe(engine)

        with engine.connect() as connection:
            reference = connection.execute(
                text("SELECT stripe_payment_intent_id FROM stripe_checkout_sessions")
            ).scalar_one()
        assert reference == STRIPE_PAYMENT
        assert _refunded(engine, intent.id) == 0

    def test_concurrent_refunds_never_exceed_the_capture_and_stripe_is_called_only_when_accepted(
        self, engine: Engine
    ) -> None:
        intent = _paid_through_stripe(engine)
        stripe = CountingStripe(pause=0.05)
        attempts = [(f"refund-{n:04d}", 1000) for n in range(5)]  # capturé : 2 500

        outcomes = _run(engine, intent, stripe, attempts)

        assert outcomes == ["conflict"] * 3 + ["ok"] * 2
        assert stripe.calls == 2
        assert _refunded(engine, intent.id) == 2000
        with Session(engine) as session:
            assert session.scalar(select(func.sum(RefundModel.amount))) == 2000
            assert session.scalar(select(func.count()).select_from(RefundModel)) == 2

    def test_the_same_key_sent_concurrently_refunds_once_at_stripe(self, engine: Engine) -> None:
        intent = _paid_through_stripe(engine)
        stripe = CountingStripe(pause=0.1)

        _run(engine, intent, stripe, [("refund-same-key", 1000)] * 4)

        assert stripe.calls == 1
        assert _refunded(engine, intent.id) == 1000

    def test_the_ledger_stays_coherent_after_payment_and_stripe_refunds(
        self, engine: Engine
    ) -> None:
        intent = _paid_through_stripe(engine)
        stripe = CountingStripe()
        use_case = _use_case(engine, stripe)
        use_case.execute(_command(intent, "refund-0001", 1000))
        use_case.execute(_command(intent, "refund-0002", 1500))

        assert _coherent(engine)
        assert _refunded(engine, intent.id) == 2500

    def test_the_migration_adds_only_nullable_columns(self, engine: Engine) -> None:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT table_name, column_name, is_nullable FROM information_schema.columns "
                    "WHERE column_name IN ('stripe_payment_intent_id', 'stripe_refund_id')"
                )
            ).all()
        assert {(r[0], r[1], r[2]) for r in rows} == {
            ("stripe_checkout_sessions", "stripe_payment_intent_id", "YES"),
            ("refunds", "stripe_refund_id", "YES"),
        }
