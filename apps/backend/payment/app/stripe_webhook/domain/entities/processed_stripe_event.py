from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class ProcessedStripeEvent:
    """Trace d'un événement Stripe déjà traité : l'identifiant unique est la garantie d'idempotence."""

    event_id: str
    event_type: str
    outcome: str
    intent_id: UUID
    processed_at: datetime
