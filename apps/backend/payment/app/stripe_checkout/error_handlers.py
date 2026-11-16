from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.stripe_checkout.domain.exceptions.intent_not_checkoutable_error import (
    IntentNotCheckoutableError,
)
from app.stripe_checkout.domain.exceptions.stripe_checkout_unavailable_error import (
    StripeCheckoutUnavailableError,
)
from app.stripe_checkout.domain.exceptions.stripe_not_enabled_error import StripeNotEnabledError


async def _not_checkoutable(_: Request, __: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": "Payment intent cannot be paid"},
    )


async def _unavailable(_: Request, __: Exception) -> JSONResponse:
    # Jamais le motif ni le corps de Stripe : le détail est dans le journal (stripe.checkout_session_failed).
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Payment provider unavailable"},
        headers={"Retry-After": "5"},
    )


async def _not_enabled(_: Request, __: Exception) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "Not Found"})


def register_stripe_checkout_error_handlers(application: FastAPI) -> None:
    application.add_exception_handler(IntentNotCheckoutableError, _not_checkoutable)
    application.add_exception_handler(StripeCheckoutUnavailableError, _unavailable)
    application.add_exception_handler(StripeNotEnabledError, _not_enabled)
