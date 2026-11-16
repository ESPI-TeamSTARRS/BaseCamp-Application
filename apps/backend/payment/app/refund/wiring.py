from datetime import UTC, datetime

from sqlalchemy import Engine

from app.core.config import Settings
from app.core.events.business_event_logger import BusinessEventLogger
from app.refund.application.record_rejected_refund import RecordRejectedRefundUseCase
from app.refund.application.refund_payment import RefundPaymentUseCase
from app.refund.application.refund_services import RefundServices
from app.refund.domain.interfaces.stripe_refund_gateway import StripeRefundGateway
from app.refund.infrastructure.gateways.http_stripe_refund_gateway import HttpStripeRefundGateway
from app.refund.infrastructure.repositories.sqlalchemy_refund_unit_of_work import (
    SqlAlchemyRefundUnitOfWork,
)


def build_stripe_refund_gateway(settings: Settings) -> StripeRefundGateway | None:
    """Passerelle de remboursement de Stripe en mode `stripe`, rien en mode simulé."""
    key = settings.stripe_secret_key
    if settings.payment_provider != "stripe" or key is None:
        return None
    return HttpStripeRefundGateway(
        settings.stripe_api_base_url, key, settings.stripe_api_timeout_seconds
    )


def build_refund_services(
    engine: Engine, events: BusinessEventLogger, stripe_gateway: StripeRefundGateway | None = None
) -> RefundServices:
    """Assemble les cas d'usage du remboursement avec leurs adaptateurs d'infrastructure."""
    return RefundServices(
        refund=RefundPaymentUseCase(
            SqlAlchemyRefundUnitOfWork(engine),
            lambda: datetime.now(UTC),
            events,
            stripe_gateway=stripe_gateway,
        ),
        record_rejection=RecordRejectedRefundUseCase(events),
    )
