class StripeRefundUnavailableError(Exception):
    """Stripe n'a pas pu rembourser (refus, délai, indisponible). Code fixe : jamais un corps de réponse."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
