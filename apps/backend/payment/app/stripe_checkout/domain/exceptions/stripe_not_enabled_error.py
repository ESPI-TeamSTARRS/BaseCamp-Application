class StripeNotEnabledError(Exception):
    """Le fournisseur Stripe n'est pas activé (`APP_PAYMENT_PROVIDER` n'est pas `stripe`)."""
