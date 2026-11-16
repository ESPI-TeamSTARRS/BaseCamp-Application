class InvalidStripeSignatureError(Exception):
    """Webhook refusé avant tout effet. `reason` est un code fixe : jamais le corps, l'en-tête ni un secret."""

    def __init__(self, reason: str) -> None:
        super().__init__("invalid stripe signature")
        self.reason = reason
