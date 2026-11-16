class StripeApiError(Exception):
    """Échec d'un appel à l'API de Stripe, réduit à un code fixe (jamais un corps de réponse ni une clé)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
