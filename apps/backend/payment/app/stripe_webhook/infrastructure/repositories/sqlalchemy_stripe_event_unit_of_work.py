from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.ledger.infrastructure.repositories.sqlalchemy_ledger_repository import (
    SqlAlchemyLedgerRepository,
)
from app.stripe_webhook.domain.interfaces.stripe_event_session import StripeEventSession
from app.stripe_webhook.domain.interfaces.stripe_event_unit_of_work import StripeEventUnitOfWork
from app.stripe_webhook.infrastructure.repositories.sqlalchemy_stripe_event_session import (
    SqlAlchemyStripeEventSession,
)
from app.webhook.infrastructure.repositories.sqlalchemy_outbox_repository import (
    SqlAlchemyOutboxRepository,
)


class SqlAlchemyStripeEventUnitOfWork(StripeEventUnitOfWork):
    """Une seule transaction : verrou de l'intention, état, grand livre, outbox et trace de l'événement."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def begin(self) -> Iterator[StripeEventSession]:
        with Session(self._engine) as session:
            try:
                yield SqlAlchemyStripeEventSession(
                    session,
                    SqlAlchemyLedgerRepository(session),
                    SqlAlchemyOutboxRepository(session),
                )
                session.commit()
            except BaseException:
                session.rollback()
                raise
