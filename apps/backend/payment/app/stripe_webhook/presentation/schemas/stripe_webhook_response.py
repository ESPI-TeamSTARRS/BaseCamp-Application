from pydantic import BaseModel


class StripeWebhookResponse(BaseModel):
    """Accusé de réception : Stripe ne lit que le code 2xx, jamais le détail."""

    received: bool
