import time

from app.core.config import Settings
from app.core.events.business_event_logger import BusinessEventLogger
from app.stripe_webhook.application.receive_stripe_webhook import ReceiveStripeWebhookUseCase
from app.stripe_webhook.domain.services.stripe_signature_verifier import StripeSignatureVerifier


def build_receive_stripe_webhook(
    settings: Settings, events: BusinessEventLogger
) -> ReceiveStripeWebhookUseCase:
    """Assemble la réception : sans secret de webhook configuré, toute requête est refusée (400)."""
    secret = settings.stripe_webhook_secret
    secrets = [secret.get_secret_value()] if secret is not None else []
    verifier = StripeSignatureVerifier(
        secrets, settings.stripe_webhook_tolerance_seconds, lambda: int(time.time())
    )
    return ReceiveStripeWebhookUseCase(verifier, events)
