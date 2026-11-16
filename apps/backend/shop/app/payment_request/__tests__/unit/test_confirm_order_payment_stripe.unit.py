"""En mode Stripe, la confirmation synchrone de la boutique est refusée (WORK-91)."""

import pytest

from app.core.__tests__.in_memory_business_event_logger import InMemoryBusinessEventLogger
from app.order.domain.enums.order_status import OrderStatus
from app.payment_request.__tests__.fakes import (
    ORDER_ID,
    FakePaymentUnitOfWork,
    PaymentStore,
    RecordingGateway,
    store_with,
)
from app.payment_request.application.confirm_order_payment import ConfirmOrderPaymentUseCase
from app.payment_request.application.order_not_payable_error import OrderNotPayableError
from app.payment_request.application.payment_order_not_found_error import (
    PaymentOrderNotFoundError,
)


def _use_case(
    store: PaymentStore, gateway: RecordingGateway, events: InMemoryBusinessEventLogger
) -> ConfirmOrderPaymentUseCase:
    return ConfirmOrderPaymentUseCase(
        lambda: FakePaymentUnitOfWork(store), gateway, events, stripe_checkout=True
    )


class TestConfirmInStripeMode:
    def test_the_synchronous_confirmation_is_refused_and_nothing_changes(self) -> None:
        store, gateway, events = store_with(), RecordingGateway(), InMemoryBusinessEventLogger()

        with pytest.raises(OrderNotPayableError) as error:
            _use_case(store, gateway, events).execute(ORDER_ID, "pm_test_success")

        assert error.value.message == "Le paiement se fait sur la page Stripe."
        assert gateway.confirmed == []
        assert store.orders[ORDER_ID].status is OrderStatus.PENDING_PAYMENT
        (event,) = events.events
        assert (event.event, event.reason) == ("order.payment_rejected", "payment_by_stripe")

    def test_an_unknown_order_is_still_not_found(self) -> None:
        with pytest.raises(PaymentOrderNotFoundError):
            _use_case(PaymentStore(), RecordingGateway(), InMemoryBusinessEventLogger()).execute(
                ORDER_ID, "pm_test_success"
            )

    def test_a_paid_order_keeps_its_usual_refusal(self) -> None:
        with pytest.raises(OrderNotPayableError) as error:
            _use_case(
                store_with(OrderStatus.PAID), RecordingGateway(), InMemoryBusinessEventLogger()
            ).execute(ORDER_ID, "pm_test_success")

        assert error.value.reason == "order_already_paid"
