from dataclasses import dataclass


@dataclass(frozen=True)
class CheckoutSessionRef:
    """Page de paiement hébergée par Stripe : seule son adresse intéresse la boutique."""

    url: str
