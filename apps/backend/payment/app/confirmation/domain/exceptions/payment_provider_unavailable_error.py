class PaymentProviderUnavailableError(Exception):
    """Le fournisseur de paiement demandé n'a pas d'adaptateur branché (message fixe, sans secret)."""
