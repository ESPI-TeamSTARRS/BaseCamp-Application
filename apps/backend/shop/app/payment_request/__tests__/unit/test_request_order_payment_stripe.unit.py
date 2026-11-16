"""Paiement d'une commande par la page hébergée de Stripe (WORK-91) : cas d'usage de la demande."""

import pytest

from app.core.__tests__.in_memory_business_event_logger import InMemoryBusinessEventLogger
from app.core.events.outcome import Outcome
from app.order.domain.enums.order_status import OrderStatus
from app.pay.domain.exceptions.payment_rejected_error import PaymentRejectedError
from app.pay.domain.exceptions.payment_unavailable_error import PaymentUnavailableError
from app.payment_request.__tests__.fakes import (
    ORDER_ID,
    FakePaymentUnitOfWork,
    PaymentStore,
    RecordingGateway,
    store_with,
)
from app.payment_request.application.order_not_payable_error import OrderNotPayableError
from app.payment_request.application.request_order_payment import RequestOrderPaymentUseCase


class _Harness:
    def __init__(self, store: PaymentStore, gateway: RecordingGateway | None = None) -> None:
        self.store = store
        self.gateway = gateway or RecordingGateway()
        self.events = InMemoryBusinessEventLogger()
        self.use_case = RequestOrderPaymentUseCase(
            lambda: FakePaymentUnitOfWork(store), self.gateway, self.events, stripe_checkout=True
        )


class TestStripeCheckout:
    def test_the_stripe_page_address_is_returned_and_no_test_method_is_offered(self) -> None:
        harness = _Harness(store_with())

        payment = harness.use_case.execute(ORDER_ID)

        assert payment.created is True
        assert payment.checkout_url == "https://checkout.stripe.com/c/pay/pi-1"
        assert payment.payment_methods == ()
        assert harness.gateway.checkouts == ["pi-1"]

    def test_the_amount_sent_to_the_provider_is_the_stored_total(self) -> None:
        harness = _Harness(store_with())

        harness.use_case.execute(ORDER_ID)

        assert harness.gateway.created == [("BC-ABCDEFGHJK", 4490, "EUR", "order-BC-ABCDEFGHJK-1")]

    def test_asking_again_replays_the_same_intent_and_asks_for_the_same_page(self) -> None:
        harness = _Harness(store_with())
        first = harness.use_case.execute(ORDER_ID)

        second = harness.use_case.execute(ORDER_ID)

        assert second.created is False
        assert second.intent.id == first.intent.id
        assert second.checkout_url == first.checkout_url
        assert len(harness.gateway.created) == 1

    def test_the_order_stays_pending_and_is_never_marked_paid_here(self) -> None:
        harness = _Harness(store_with())

        harness.use_case.execute(ORDER_ID)

        assert harness.store.orders[ORDER_ID].status is OrderStatus.PENDING_PAYMENT

    @pytest.mark.parametrize(
        ("status", "reason"),
        [(OrderStatus.PAID, "order_already_paid"), (OrderStatus.EXPIRED, "order_expired")],
    )
    def test_a_paid_or_expired_order_is_refused_before_any_call(
        self, status: OrderStatus, reason: str
    ) -> None:
        harness = _Harness(store_with(status))

        with pytest.raises(OrderNotPayableError) as error:
            harness.use_case.execute(ORDER_ID)

        assert error.value.reason == reason
        assert harness.gateway.created == [] and harness.gateway.checkouts == []

    def test_an_unavailable_page_service_is_logged_and_the_request_can_be_replayed(self) -> None:
        gateway = RecordingGateway(checkout_error=PaymentUnavailableError())
        harness = _Harness(store_with(), gateway)

        with pytest.raises(PaymentUnavailableError):
            harness.use_case.execute(ORDER_ID)

        assert harness.store.orders[ORDER_ID].status is OrderStatus.PENDING_PAYMENT
        event = harness.events.events[-1]
        assert (event.event, event.outcome, event.reason) == (
            "order.payment_unavailable",
            Outcome.ERROR,
            "payment_unavailable",
        )
        gateway.checkout_error = None
        replay = harness.use_case.execute(ORDER_ID)
        assert replay.checkout_url is not None and len(gateway.created) == 1

    def test_a_refusal_of_the_provider_is_propagated(self) -> None:
        gateway = RecordingGateway(checkout_error=PaymentRejectedError(conflict=True))
        harness = _Harness(store_with(), gateway)

        with pytest.raises(PaymentRejectedError):
            harness.use_case.execute(ORDER_ID)

    def test_the_log_never_holds_the_page_address(self) -> None:
        harness = _Harness(store_with())

        harness.use_case.execute(ORDER_ID)

        names = [event.event for event in harness.events.events]
        assert names == ["order.payment_requested", "order.checkout_provided"]
        assert "stripe.com" not in repr(harness.events.events)

    def test_without_stripe_mode_no_page_is_requested(self) -> None:
        gateway = RecordingGateway()
        use_case = RequestOrderPaymentUseCase(
            lambda: FakePaymentUnitOfWork(store_with()), gateway, InMemoryBusinessEventLogger()
        )

        payment = use_case.execute(ORDER_ID)

        assert payment.checkout_url is None and gateway.checkouts == []
        assert payment.payment_methods == ("pm_test_success", "pm_test_declined")
