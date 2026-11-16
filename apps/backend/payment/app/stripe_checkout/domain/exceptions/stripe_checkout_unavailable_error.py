class StripeCheckoutUnavailableError(Exception):
    """Stripe n'a pas pu créer la session. Le motif est un code fixe : jamais un corps de réponse."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
