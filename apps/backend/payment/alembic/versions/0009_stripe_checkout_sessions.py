"""Sessions Checkout Stripe (WORK-88) : une session par intention.

Table additive : aucune table ni colonne existante n'est modifiée, l'ancien code l'ignore et reste valide
sur le schéma migré. `intent_id` est la clé primaire (une seule session par intention) et `session_id`
est unique (une session Stripe ne sert qu'une intention).

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stripe_checkout_sessions",
        sa.Column("intent_id", sa.Uuid(), sa.ForeignKey("payment_intents.id"), primary_key=True),
        sa.Column("session_id", sa.String(255), nullable=False, unique=True),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("stripe_checkout_sessions")
