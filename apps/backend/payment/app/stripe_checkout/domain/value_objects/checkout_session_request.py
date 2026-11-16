from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class CheckoutSessionRequest:
    """Ce que Stripe reçoit : le montant vient de l'intention stockée, jamais de l'appelant."""

    intent_id: UUID
    order_reference: str
    amount_cents: int
    currency: str
    success_url: str
    cancel_url: str
    idempotency_key: str
