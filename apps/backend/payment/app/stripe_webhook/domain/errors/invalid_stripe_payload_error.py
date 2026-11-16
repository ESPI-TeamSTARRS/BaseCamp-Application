class InvalidStripePayloadError(Exception):
    """Le corps d'un webhook authentique n'est pas un événement Stripe lisible (code fixe, sans écho)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
