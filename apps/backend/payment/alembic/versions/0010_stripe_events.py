"""Événements Stripe traités (WORK-89) : l'identifiant d'événement est la garantie d'idempotence.

Table additive : aucune table ni colonne existante n'est modifiée, l'ancien code l'ignore et reste valide
sur le schéma migré. `event_id` est la clé primaire (un événement Stripe n'a d'effet qu'une fois).

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stripe_events",
        sa.Column("event_id", sa.String(255), primary_key=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("intent_id", sa.Uuid(), sa.ForeignKey("payment_intents.id"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_stripe_events_intent_id", "stripe_events", ["intent_id"])


def downgrade() -> None:
    op.drop_index("ix_stripe_events_intent_id", table_name="stripe_events")
    op.drop_table("stripe_events")
