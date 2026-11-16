from app.core.events.business_event import BusinessEvent
from app.core.events.business_event_logger import BusinessEventLogger
from app.stripe_webhook.application import stripe_webhook_event_names as names
from app.stripe_webhook.application.process_stripe_event import ProcessStripeEventUseCase
from app.stripe_webhook.domain.errors.invalid_stripe_payload_error import (
    InvalidStripePayloadError,
)
from app.stripe_webhook.domain.errors.invalid_stripe_signature_error import (
    InvalidStripeSignatureError,
)
from app.stripe_webhook.domain.services.stripe_event_parser import StripeEventParser
from app.stripe_webhook.domain.services.stripe_signature_verifier import StripeSignatureVerifier


class ReceiveStripeWebhookUseCase:
    """Authentifie un webhook de Stripe puis traite l'événement : la signature passe avant TOUT le reste.

    Une signature refusée n'ouvre aucune lecture d'état. Le corps brut, l'en-tête et les secrets ne sont
    jamais écrits dans un journal : seuls la taille du corps et un code de refus le sont.
    """

    def __init__(
        self,
        verifier: StripeSignatureVerifier,
        parser: StripeEventParser,
        processor: ProcessStripeEventUseCase,
        events: BusinessEventLogger,
    ) -> None:
        self._verifier = verifier
        self._parser = parser
        self._processor = processor
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
        try:
            event = self._parser.parse(raw_body)
        except InvalidStripePayloadError as error:
            self._events.log(BusinessEvent.denied(names.STRIPE_EVENT_IGNORED, error.reason))
            raise
        self._processor.execute(event)
