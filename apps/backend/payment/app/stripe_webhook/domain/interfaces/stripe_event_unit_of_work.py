from abc import ABC, abstractmethod
from contextlib import AbstractContextManager

from app.stripe_webhook.domain.interfaces.stripe_event_session import StripeEventSession


class StripeEventUnitOfWork(ABC):
    @abstractmethod
    def begin(self) -> AbstractContextManager[StripeEventSession]:
        """Ouvre une transaction : validée à la sortie du bloc, annulée si une exception survient."""
