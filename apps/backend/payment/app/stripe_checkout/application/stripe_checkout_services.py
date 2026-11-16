from dataclasses import dataclass

from app.stripe_checkout.application.create_checkout_session import CreateCheckoutSessionUseCase


@dataclass(frozen=True)
class StripeCheckoutServices:
    """Cas d'usage de la tranche, câblés dans wiring.py et lus par la présentation."""

    create: CreateCheckoutSessionUseCase
