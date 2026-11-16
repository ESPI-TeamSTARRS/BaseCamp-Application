from abc import ABC, abstractmethod
from contextlib import AbstractContextManager

from app.stripe_checkout.domain.interfaces.checkout_session_session import CheckoutSessionSession


class CheckoutSessionUnitOfWork(ABC):
    @abstractmethod
    def begin(self) -> AbstractContextManager[CheckoutSessionSession]:
        """Ouvre une transaction : validée à la sortie du bloc, annulée si une exception survient."""
