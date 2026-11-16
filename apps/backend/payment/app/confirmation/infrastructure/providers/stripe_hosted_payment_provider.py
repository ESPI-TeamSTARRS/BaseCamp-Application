from app.confirmation.domain.enums.payment_method import PaymentMethod
from app.confirmation.domain.enums.provider_outcome import ProviderOutcome
from app.confirmation.domain.exceptions.intent_not_confirmable_error import (
    IntentNotConfirmableError,
)
from app.confirmation.domain.interfaces.payment_provider import PaymentProvider
from app.intent.domain.value_objects.amount import Amount


class StripeHostedPaymentProvider(PaymentProvider):
    """Mode `stripe` : le client paie sur la page hébergée de Stripe, jamais par l'API synchrone.

    La confirmation synchrone est refusée (409) : une intention ne passe `succeeded` que sur un
    événement signé de Stripe, jamais sur la foi d'un appel de la boutique, et jamais par repli sur le
    fournisseur simulé.
    """

    def authorize(self, method: PaymentMethod, amount: Amount) -> ProviderOutcome:
        raise IntentNotConfirmableError
