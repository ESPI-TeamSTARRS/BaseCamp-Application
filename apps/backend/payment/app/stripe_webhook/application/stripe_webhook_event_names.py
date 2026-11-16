"""Noms des événements du webhook entrant de Stripe (catalogue : docs/services/payment.md)."""

STRIPE_WEBHOOK_VERIFIED = "stripe.webhook_verified"
STRIPE_WEBHOOK_REJECTED = "stripe.webhook_rejected"
STRIPE_EVENT_PROCESSED = "stripe.event_processed"
STRIPE_EVENT_IGNORED = "stripe.event_ignored"
STRIPE_EVENT_REPLAYED = "stripe.event_replayed"
