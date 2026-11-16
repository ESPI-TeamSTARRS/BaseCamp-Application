from abc import ABC, abstractmethod
from uuid import UUID

from app.intent.domain.entities.payment_intent import PaymentIntent
from app.ledger.domain.entities.ledger_transaction import LedgerTransaction
from app.stripe_webhook.domain.entities.processed_stripe_event import ProcessedStripeEvent
from app.webhook.domain.entities.outbox_event import OutboxEvent


class StripeEventSession(ABC):
    """Une transaction : intention verrouillée, trace de l'événement, état, grand livre et outbox d'un bloc."""

    @abstractmethod
    def is_processed(self, event_id: str) -> bool: ...

    @abstractmethod
    def lock_intent(self, intent_id: UUID) -> PaymentIntent | None:
        """Lit l'intention en posant un verrou de ligne jusqu'à la fin de la transaction."""

    @abstractmethod
    def get_checkout_session_id(self, intent_id: UUID) -> str | None:
        """Identifiant de la session Checkout créée pour l'intention (WORK-88), s'il y en a une."""

    @abstractmethod
    def save_payment_reference(self, intent_id: UUID, stripe_payment_intent_id: str) -> None:
        """Retient le paiement Stripe de l'intention (pour le rembourser plus tard)."""

    @abstractmethod
    def has_stripe_refund(self, stripe_refund_id: str) -> bool:
        """Ce remboursement Stripe a-t-il été émis par nous (et enregistré) ?"""

    @abstractmethod
    def record_event(self, event: ProcessedStripeEvent) -> None:
        """Lève DuplicateStripeEventError si l'identifiant d'événement est déjà enregistré."""

    @abstractmethod
    def save_settlement(
        self, intent: PaymentIntent, ledger: LedgerTransaction | None, outbox_event: OutboxEvent
    ) -> None:
        """Écrit l'état, le grand livre (paiement encaissé seulement) et l'événement à publier."""
