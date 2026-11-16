from datetime import UTC, datetime

from sqlalchemy import Engine

from app.core.config import Settings
from app.core.events.business_event_logger import BusinessEventLogger
from app.stripe_checkout.application.create_checkout_session import CreateCheckoutSessionUseCase
from app.stripe_checkout.application.stripe_checkout_services import StripeCheckoutServices
from app.stripe_checkout.domain.interfaces.stripe_checkout_gateway import StripeCheckoutGateway
from app.stripe_checkout.infrastructure.gateways.http_stripe_checkout_gateway import (
    HttpStripeCheckoutGateway,
)
from app.stripe_checkout.infrastructure.repositories.sqlalchemy_checkout_session_unit_of_work import (
    SqlAlchemyCheckoutSessionUnitOfWork,
)


def build_stripe_checkout_gateway(settings: Settings) -> StripeCheckoutGateway | None:
    """Passerelle HTTP de Stripe en mode `stripe`, rien en mode simulé (la route répond alors 404)."""
    if settings.payment_provider != "stripe":
        return None
    key = settings.stripe_secret_key
    if key is None or not settings.stripe_success_url or not settings.stripe_cancel_url:
        raise ValueError("stripe mode requires APP_STRIPE_SUCCESS_URL and APP_STRIPE_CANCEL_URL")
    return HttpStripeCheckoutGateway(
        settings.stripe_api_base_url, key, settings.stripe_api_timeout_seconds
    )


def build_stripe_checkout_services(
    engine: Engine,
    settings: Settings,
    events: BusinessEventLogger,
    gateway: StripeCheckoutGateway | None,
) -> StripeCheckoutServices:
    """Assemble le cas d'usage; sans passerelle (mode simulé), la route répond 404."""
    return StripeCheckoutServices(
        create=CreateCheckoutSessionUseCase(
            SqlAlchemyCheckoutSessionUnitOfWork(engine),
            gateway,
            settings.stripe_success_url,
            settings.stripe_cancel_url,
            lambda: datetime.now(UTC),
            events,
        )
    )
