from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.stripe_webhook.domain.errors.invalid_stripe_payload_error import (
    InvalidStripePayloadError,
)
from app.stripe_webhook.domain.errors.invalid_stripe_signature_error import (
    InvalidStripeSignatureError,
)

REJECTION_BODY = {"detail": "Invalid signature."}
PAYLOAD_REJECTION_BODY = {"detail": "Invalid payload."}


def register_stripe_webhook_error_handlers(application: FastAPI) -> None:
    """Toute signature refusée donne le même 400, sans détail (ni motif, ni écho du corps)."""

    @application.exception_handler(InvalidStripeSignatureError)
    async def _invalid_signature(_: Request, __: InvalidStripeSignatureError) -> JSONResponse:
        return JSONResponse(status_code=400, content=REJECTION_BODY)

    @application.exception_handler(InvalidStripePayloadError)
    async def _invalid_payload(_: Request, __: InvalidStripePayloadError) -> JSONResponse:
        # Corps authentique mais illisible : même assainissement, jamais le motif ni le corps.
        return JSONResponse(status_code=400, content=PAYLOAD_REJECTION_BODY)
