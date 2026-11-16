class DuplicateStripeEventError(Exception):
    """L'identifiant d'événement Stripe est déjà enregistré (rejeu ou livraison concurrente)."""
