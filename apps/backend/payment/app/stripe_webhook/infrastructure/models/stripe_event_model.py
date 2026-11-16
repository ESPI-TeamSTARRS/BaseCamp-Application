from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.intent.infrastructure.models.base import Base


class StripeEventModel(Base):
    __tablename__ = "stripe_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(16))
    intent_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("payment_intents.id"), index=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
