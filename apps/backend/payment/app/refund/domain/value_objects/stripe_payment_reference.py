from dataclasses import dataclass


@dataclass(frozen=True)
class StripePaymentReference:
    """Une intention payée par Stripe. `payment_intent_id` manque si l'événement de paiement n'en portait pas."""

    payment_intent_id: str | None
