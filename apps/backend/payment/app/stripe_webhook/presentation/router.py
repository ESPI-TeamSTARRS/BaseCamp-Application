from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request

from app.stripe_webhook.application.receive_stripe_webhook import ReceiveStripeWebhookUseCase
from app.stripe_webhook.presentation.dependencies import get_receive_stripe_webhook_use_case
from app.stripe_webhook.presentation.schemas.stripe_webhook_response import StripeWebhookResponse

router = APIRouter(tags=["stripe-webhook"])


@router.post("/stripe/webhook", response_model=StripeWebhookResponse)
async def receive_stripe_webhook(
    request: Request,
    use_case: Annotated[ReceiveStripeWebhookUseCase, Depends(get_receive_stripe_webhook_use_case)],
    signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> StripeWebhookResponse:
    """Webhook entrant de Stripe : sans jeton d'appelant, seule la signature fait foi.

    Le corps brut est lu tel quel (ce sont ces octets qui sont signés), avant tout décodage.
    """
    raw_body = await request.body()
    use_case.execute(raw_body, signature)
    return StripeWebhookResponse(received=True)
