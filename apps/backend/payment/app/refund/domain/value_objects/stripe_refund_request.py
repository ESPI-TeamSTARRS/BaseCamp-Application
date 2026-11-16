from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class StripeRefundRequest:
    """Ce que Stripe reçoit : le paiement Stripe à rembourser et le montant (centimes), côté serveur."""

    intent_id: UUID
    stripe_payment_intent_id: str
    amount_cents: int
    origin: str
    idempotency_key: str
