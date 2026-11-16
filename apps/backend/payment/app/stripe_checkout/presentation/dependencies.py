from typing import cast

from fastapi import Request

from app.stripe_checkout.application.stripe_checkout_services import StripeCheckoutServices


def get_stripe_checkout_services(request: Request) -> StripeCheckoutServices:
    """Les cas d'usage sont câblés dans main.py (la présentation n'importe pas l'infrastructure)."""
    return cast(StripeCheckoutServices, request.app.state.stripe_checkout_services)
