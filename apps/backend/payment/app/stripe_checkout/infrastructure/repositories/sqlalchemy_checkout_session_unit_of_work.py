from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.stripe_checkout.domain.interfaces.checkout_session_session import CheckoutSessionSession
from app.stripe_checkout.domain.interfaces.checkout_session_unit_of_work import (
    CheckoutSessionUnitOfWork,
)
from app.stripe_checkout.infrastructure.repositories.sqlalchemy_checkout_session_session import (
    SqlAlchemyCheckoutSessionSession,
)


class SqlAlchemyCheckoutSessionUnitOfWork(CheckoutSessionUnitOfWork):
    """Une transaction courte : verrou de l'intention, lecture et écriture de la session stockée."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def begin(self) -> Iterator[CheckoutSessionSession]:
        with Session(self._engine) as session:
            try:
                yield SqlAlchemyCheckoutSessionSession(session)
                session.commit()
            except BaseException:
                session.rollback()
                raise
