from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class CheckoutSession:
    """Session Checkout hébergée par Stripe, liée à une seule intention (1 pour 1)."""

    intent_id: UUID
    session_id: str
    url: str
    created_at: datetime
