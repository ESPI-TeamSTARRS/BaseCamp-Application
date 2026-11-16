"""Isolation de payment : aucun appel venant de l'exterieur ne doit l'atteindre."""

import os
import socket
from typing import ClassVar

import httpx
import pytest

BASE_URL = os.environ.get("E2E_BASE_URL", "")


@pytest.fixture
def client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, timeout=5.0)


class TestPaymentIsolation:
    @pytest.mark.parametrize("path", ["/health", "/health/ready", "/metrics"])
    def test_payment_is_not_routed_by_the_gateway(self, client: httpx.Client, path: str) -> None:
        response = client.get(f"/api/payment{path}")

        assert response.status_code == 404

    def test_payment_hostname_does_not_resolve_from_the_host(self) -> None:
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo("payment", 8000)

    def test_payment_is_not_published_on_the_host(self) -> None:
        with pytest.raises(OSError), socket.create_connection(("127.0.0.1", 8000), timeout=2):
            pass


class TestStripeWebhookThroughTheGateway:
    """Seule route de payment servie par la passerelle (WORK-87) : POST /api/stripe/webhook."""

    PATH = "/api/stripe/webhook"
    REJECTION: ClassVar[dict[str, str]] = {"detail": "Invalid signature."}

    def test_an_unsigned_post_reaches_payment_and_is_refused_without_detail(
        self, client: httpx.Client
    ) -> None:
        response = client.post(
            self.PATH, content=b"{}", headers={"Content-Type": "application/json"}
        )

        assert (response.status_code, response.json()) == (400, self.REJECTION)

    def test_a_forged_signature_gets_the_same_refusal(self, client: httpx.Client) -> None:
        header = "t=1790000000,v1=" + "0" * 64

        response = client.post(self.PATH, content=b"{}", headers={"Stripe-Signature": header})

        assert (response.status_code, response.json()) == (400, self.REJECTION)

    @pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
    def test_any_other_method_is_refused_with_405(self, client: httpx.Client, method: str) -> None:
        assert client.request(method, self.PATH).status_code == 405

    def test_a_body_above_the_gateway_limit_is_refused_with_413(self, client: httpx.Client) -> None:
        response = client.post(self.PATH, content=b"x" * 70_000)

        assert response.status_code == 413

    @pytest.mark.parametrize(
        "path",
        [
            "/api/stripe",
            "/api/stripe/",
            "/api/stripe/webhook/extra",
            "/api/stripe/metrics",
            "/api/stripe/health",
        ],
    )
    def test_no_other_stripe_path_reaches_payment(self, client: httpx.Client, path: str) -> None:
        response = client.post(path, content=b"{}")

        assert response.status_code == 404
        assert "Invalid signature" not in response.text

    def test_the_webhook_route_does_not_open_the_rest_of_payment(
        self, client: httpx.Client
    ) -> None:
        for path in ("/api/payment/payments/intents", "/api/stripe/payments/intents"):
            assert client.get(path).status_code == 404
