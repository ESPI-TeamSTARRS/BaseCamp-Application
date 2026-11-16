from typing import cast

from fastapi import Request

from app.stripe_webhook.application.receive_stripe_webhook import ReceiveStripeWebhookUseCase


def get_receive_stripe_webhook_use_case(request: Request) -> ReceiveStripeWebhookUseCase:
    """Le cas d'usage est câblé dans main.py (la présentation n'importe pas l'infrastructure)."""
    return cast(ReceiveStripeWebhookUseCase, request.app.state.receive_stripe_webhook_use_case)
