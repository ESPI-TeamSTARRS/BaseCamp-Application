import time
from datetime import UTC, datetime

from sqlalchemy import Engine

from app.core.config import Settings
from app.core.events.business_event_logger import BusinessEventLogger
from app.refund.domain.interfaces.stripe_refund_gateway import StripeRefundGateway
from app.stripe_webhook.application.process_stripe_event import ProcessStripeEventUseCase
from app.stripe_webhook.application.receive_stripe_webhook import ReceiveStripeWebhookUseCase
from app.stripe_webhook.domain.services.stripe_event_parser import StripeEventParser
from app.stripe_webhook.domain.services.stripe_signature_verifier import StripeSignatureVerifier
from app.stripe_webhook.infrastructure.repositories.sqlalchemy_stripe_event_unit_of_work import (
    SqlAlchemyStripeEventUnitOfWork,
)


def build_receive_stripe_webhook(
    engine: Engine,
    settings: Settings,
    events: BusinessEventLogger,
    refund_gateway: StripeRefundGateway | None = None,
) -> ReceiveStripeWebhookUseCase:
    """Assemble la réception : sans secret de webhook configuré, toute requête est refusée (400)."""
    secret = settings.stripe_webhook_secret
    secrets = [secret.get_secret_value()] if secret is not None else []
    verifier = StripeSignatureVerifier(
        secrets, settings.stripe_webhook_tolerance_seconds, lambda: int(time.time())
    )
    processor = ProcessStripeEventUseCase(
        SqlAlchemyStripeEventUnitOfWork(engine), lambda: datetime.now(UTC), events, refund_gateway
    )
    return ReceiveStripeWebhookUseCase(verifier, StripeEventParser(), processor, events)
