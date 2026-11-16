from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.intent.presentation.dependencies import get_calling_service
from app.stripe_checkout.application.commands.create_checkout_session_command import (
    CreateCheckoutSessionCommand,
)
from app.stripe_checkout.application.stripe_checkout_services import StripeCheckoutServices
from app.stripe_checkout.presentation.dependencies import get_stripe_checkout_services
from app.stripe_checkout.presentation.schemas.checkout_session_response import (
    CheckoutSessionResponse,
)

router = APIRouter(prefix="/payments/intents", tags=["stripe-checkout"])

Services = Annotated[StripeCheckoutServices, Depends(get_stripe_checkout_services)]
Caller = Annotated[str, Depends(get_calling_service)]


@router.post(
    "/{intent_id}/checkout-session",
    response_model=CheckoutSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_checkout_session(
    intent_id: str, caller: Caller, services: Services, response: Response
) -> CheckoutSessionResponse:
    """Aucun corps : le montant vient de l'intention stockée. 201 à la création, 200 au rejeu."""
    view = services.create.execute(CreateCheckoutSessionCommand(caller=caller, intent_id=intent_id))
    if not view.created:
        response.status_code = status.HTTP_200_OK
    return CheckoutSessionResponse(
        intent_id=view.intent_id, session_id=view.session_id, checkout_url=view.url
    )
