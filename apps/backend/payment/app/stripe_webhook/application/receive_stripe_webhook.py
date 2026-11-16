from app.core.events.business_event import BusinessEvent
from app.core.events.business_event_logger import BusinessEventLogger
from app.stripe_webhook.application import stripe_webhook_event_names as names
from app.stripe_webhook.domain.errors.invalid_stripe_signature_error import (
    InvalidStripeSignatureError,
)
from app.stripe_webhook.domain.services.stripe_signature_verifier import StripeSignatureVerifier


class ReceiveStripeWebhookUseCase:
    """Authentifie un webhook de Stripe : la signature est vérifiée avant tout autre traitement.

    Pour l'instant aucun événement n'est traité (le traitement vient avec la confirmation par webhook) :
    un événement authentique est seulement acquitté et journalisé. Le corps brut, l'en-tête et les secrets
    ne sont jamais écrits dans un journal : seuls la taille du corps et un code de refus le sont.
    """

    def __init__(self, verifier: StripeSignatureVerifier, events: BusinessEventLogger) -> None:
        self._verifier = verifier
        self._events = events

    def execute(self, raw_body: bytes, signature_header: str | None) -> None:
        try:
            self._verifier.verify(signature_header, raw_body)
        except InvalidStripeSignatureError as error:
            self._events.log(BusinessEvent.denied(names.STRIPE_WEBHOOK_REJECTED, error.reason))
            raise
        self._events.log(
            BusinessEvent.success(
                names.STRIPE_WEBHOOK_VERIFIED, attributes={"body_bytes": len(raw_body)}
            )
        )
