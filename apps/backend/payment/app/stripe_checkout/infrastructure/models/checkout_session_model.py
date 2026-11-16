from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.intent.infrastructure.models.base import Base


class CheckoutSessionModel(Base):
    __tablename__ = "stripe_checkout_sessions"

    intent_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("payment_intents.id"), primary_key=True
    )
    session_id: Mapped[str] = mapped_column(String(255), unique=True)
    url: Mapped[str] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Posé à la confirmation du paiement (WORK-89/90) : le paiement Stripe à rembourser.
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255), default=None)
