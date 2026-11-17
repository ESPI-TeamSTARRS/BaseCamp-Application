"""Passerelle : une seule route atteint `payment`, `POST /api/stripe/webhook` (WORK-87).

Ces tests ne lancent aucun conteneur : ils vérifient les invariants des fichiers de la passerelle
(développement et production) et des réseaux, pour qu'une modification ne puisse pas exposer d'autre route
de `payment`, retirer une limite ou joindre `payment` à un réseau trop large.
"""

import ipaddress
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DYNAMIC = {
    "dev": ROOT / "infra" / "traefik" / "dynamic.yml",
    "prod": ROOT / "deploy" / "traefik" / "dynamic.prod.yml",
}
COMPOSE = {
    "dev": ROOT / "docker-compose.yml",
    "prod": ROOT / "deploy" / "docker-compose.prod.yml",
}
ROUTER = "stripe-webhook"
BODY_LIMIT_BYTES = 65_536  # événements Stripe : quelques Kio, 64 Kio de marge


def _http(variant: str) -> dict[str, Any]:
    document = yaml.safe_load(DYNAMIC[variant].read_text(encoding="utf-8"))
    return dict(document["http"])


def _compose(variant: str) -> dict[str, Any]:
    return dict(yaml.safe_load(COMPOSE[variant].read_text(encoding="utf-8")))


@pytest.mark.parametrize("variant", ["dev", "prod"])
class TestGatewayRoute:
    def test_only_the_stripe_webhook_router_reaches_payment(self, variant: str) -> None:
        routers = _http(variant)["routers"]

        reaching_payment = {name for name, router in routers.items() if router["service"] == "payment"}

        assert reaching_payment == {ROUTER}

    def test_the_route_is_the_exact_path_on_the_public_entrypoint_only(self, variant: str) -> None:
        router = _http(variant)["routers"][ROUTER]

        assert router["rule"] == "Path(`/api/stripe/webhook`)"
        assert router["entryPoints"] == ["web"]

    def test_no_other_router_rule_can_match_the_stripe_path_with_a_higher_priority(
        self, variant: str
    ) -> None:
        routers = _http(variant)["routers"]
        mine = routers[ROUTER]["priority"]

        competitors = [
            name
            for name, router in routers.items()
            if name != ROUTER and "/api/stripe" in router["rule"]
        ]

        assert competitors == []
        assert mine >= 200  # au-dessus des routeurs génériques (front, shop, auth)

    def test_the_route_is_rate_limited_size_limited_and_hardened_in_that_order(
        self, variant: str
    ) -> None:
        http = _http(variant)
        middlewares = http["routers"][ROUTER]["middlewares"]

        assert middlewares == [
            "stripe-webhook-rate-limit",
            "stripe-webhook-body-limit",
            "security-headers",
            "strip-api",
        ]
        limit = http["middlewares"]["stripe-webhook-body-limit"]["buffering"]
        assert limit["maxRequestBodyBytes"] == BODY_LIMIT_BYTES
        assert limit["memRequestBodyBytes"] == BODY_LIMIT_BYTES
        rate = http["middlewares"]["stripe-webhook-rate-limit"]["rateLimit"]
        assert rate["average"] > 0 and rate["burst"] > 0 and rate["period"] == "1m"

    def test_only_the_api_prefix_is_removed_so_the_service_receives_its_own_path(
        self, variant: str
    ) -> None:
        strip = _http(variant)["middlewares"]["strip-api"]["stripPrefix"]

        assert strip["prefixes"] == ["/api"]

    def test_the_payment_service_is_the_internal_hostname_with_the_default_transport(
        self, variant: str
    ) -> None:
        service = _http(variant)["services"]["payment"]["loadBalancer"]

        assert service["serversTransport"] == "default"
        assert [server["url"] for server in service["servers"]] == ["http://payment:8000"]


@pytest.mark.parametrize("variant", ["dev", "prod"])
class TestDedicatedNetwork:
    def test_a_dedicated_internal_network_joins_the_gateway_and_payment_only(
        self, variant: str
    ) -> None:
        compose = _compose(variant)

        assert compose["networks"]["payment-hook"]["internal"] is True
        members = {
            name
            for name, service in compose["services"].items()
            if "payment-hook" in service.get("networks", [])
        }
        assert members == {"traefik", "payment"}

    def test_payment_stays_off_the_public_and_application_networks(self, variant: str) -> None:
        networks = _compose(variant)["services"]["payment"]["networks"]

        assert not {"app", "edge", "web"} & set(networks)

    def test_the_gateway_does_not_join_the_other_payment_networks(self, variant: str) -> None:
        networks = _compose(variant)["services"]["traefik"]["networks"]

        assert not {"payment-net", "payment-data", "data", "auth-data"} & set(networks)


def test_the_production_hook_network_has_a_fixed_subnet_clear_of_the_vpn_and_docker() -> None:
    network = _compose("prod")["networks"]["payment-hook"]
    subnet = ipaddress.ip_network(network["ipam"]["config"][0]["subnet"])

    assert subnet == ipaddress.ip_network("10.253.0.0/24")
    assert not subnet.overlaps(ipaddress.ip_network("192.168.0.0/16"))
    assert not subnet.overlaps(ipaddress.ip_network("172.16.0.0/12"))
    assert not subnet.overlaps(ipaddress.ip_network("10.250.0.0/24"))  # mail-egress
