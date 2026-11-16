"""Références Stripe du remboursement (WORK-90) : deux colonnes nullables, ajout seul.

`stripe_checkout_sessions.stripe_payment_intent_id` : le paiement Stripe à rembourser, posé quand
l'événement de paiement est confirmé. `refunds.stripe_refund_id` : le remboursement créé chez Stripe,
pour rapprocher son événement. Aucune colonne existante n'est modifiée : l'ancien code ignore les
nouvelles et reste valide sur le schéma migré.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("stripe_checkout_sessions", sa.Column("stripe_payment_intent_id", sa.String(255)))
    op.add_column("refunds", sa.Column("stripe_refund_id", sa.String(255)))
    op.create_index("ix_refunds_stripe_refund_id", "refunds", ["stripe_refund_id"])


def downgrade() -> None:
    op.drop_index("ix_refunds_stripe_refund_id", table_name="refunds")
    op.drop_column("refunds", "stripe_refund_id")
    op.drop_column("stripe_checkout_sessions", "stripe_payment_intent_id")
