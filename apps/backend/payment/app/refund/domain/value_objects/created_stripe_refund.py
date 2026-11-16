from dataclasses import dataclass

ACCEPTED_STATUSES = frozenset({"succeeded", "pending"})


@dataclass(frozen=True)
class CreatedStripeRefund:
    """Remboursement créé chez Stripe : `failed`, `canceled`... ne sont pas des remboursements acceptés."""

    refund_id: str
    status: str

    @property
    def accepted(self) -> bool:
        return self.status in ACCEPTED_STATUSES
