class IntentNotCheckoutableError(Exception):
    """L'intention n'attend plus de paiement (confirmée, annulée, expirée...) : pas de session."""
