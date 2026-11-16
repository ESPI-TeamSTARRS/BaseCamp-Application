import time
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import Engine

from app.confirmation.application.confirm_payment import ConfirmPaymentUseCase
from app.confirmation.application.confirmation_services import ConfirmationServices
from app.confirmation.application.record_rejected_confirmation import (
    RecordRejectedConfirmationUseCase,
)
from app.confirmation.domain.interfaces.payment_provider import PaymentProvider
from app.confirmation.infrastructure.providers.internal_payment_provider import (
    InternalPaymentProvider,
)
from app.confirmation.infrastructure.providers.stripe_hosted_payment_provider import (
    StripeHostedPaymentProvider,
)
from app.confirmation.infrastructure.repositories.sqlalchemy_confirmation_unit_of_work import (
    SqlAlchemyConfirmationUnitOfWork,
)
from app.core.config import Settings
from app.core.events.business_event_logger import BusinessEventLogger


def build_payment_provider(settings: Settings, wait: Callable[[float], None]) -> PaymentProvider:
    """Fournisseur choisi par `APP_PAYMENT_PROVIDER`; le simulé est celui par défaut.

    Le mode `stripe` ne confirme jamais de façon synchrone (le client paie chez Stripe, le résultat vient
    du webhook signé) : jamais de repli silencieux sur le fournisseur simulé, qui ferait croire à un
    paiement Stripe.
    """
    if settings.payment_provider == "simulated":
        return InternalPaymentProvider(wait, settings.provider_timeout_seconds)
    return StripeHostedPaymentProvider()


def build_confirmation_services(
    engine: Engine,
    settings: Settings,
    events: BusinessEventLogger,
    wait: Callable[[float], None] = time.sleep,
) -> ConfirmationServices:
    """Assemble les cas d'usage avec le fournisseur choisi par la configuration."""
    return ConfirmationServices(
        confirm=ConfirmPaymentUseCase(
            SqlAlchemyConfirmationUnitOfWork(engine),
            build_payment_provider(settings, wait),
            lambda: datetime.now(UTC),
            events,
        ),
        record_rejection=RecordRejectedConfirmationUseCase(events),
    )
