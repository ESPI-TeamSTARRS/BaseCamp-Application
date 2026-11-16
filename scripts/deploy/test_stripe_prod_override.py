"""Surcharge d'activation de Stripe en production (deploy/docker-compose.prod.stripe.yml, WORK-93).

Ces tests ne lancent aucun conteneur : ils vérifient que la surcharge est inerte tant qu'elle n'est pas
choisie, qu'elle n'ouvre la sortie vers Internet qu'à `payment` et `payment-worker`, et qu'elle ne contient
aucun secret.
"""

import ipaddress
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "deploy" / "docker-compose.prod.yml"
OVERRIDE = ROOT / "deploy" / "docker-compose.prod.stripe.yml"
ENV_EXAMPLE = ROOT / "deploy" / "prod.env.example"
EGRESS = "stripe-egress"
STRIPE_SERVICES = {"payment", "payment-worker"}


def _load(path: Path) -> dict[str, Any]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")))


BASE_COMPOSE = _load(BASE)
OVERRIDE_COMPOSE = _load(OVERRIDE)


class TestInertByDefault:
    def test_the_base_stack_has_no_stripe_mode_secret_or_egress(self) -> None:
        text = BASE.read_text(encoding="utf-8")

        assert EGRESS not in text
        assert "payment_stripe" not in text
        assert "APP_PAYMENT_PROVIDER" not in text
        assert "APP_STRIPE" not in text

    def test_in_the_base_stack_payment_has_no_way_out_to_the_internet(self) -> None:
        networks = BASE_COMPOSE["networks"]

        for name in ("payment-net", "payment-data", "payment-hook"):
            assert networks[name]["internal"] is True
        for service in STRIPE_SERVICES:
            assert set(BASE_COMPOSE["services"][service]["networks"]) <= {
                "payment-net",
                "payment-data",
                "payment-hook",
            }

    def test_nothing_in_the_deployment_scripts_selects_the_override(self) -> None:
        scripts = "".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "scripts").glob("*.sh")
        )

        assert "docker-compose.prod.stripe.yml" not in scripts


class TestOverride:
    def test_only_payment_and_its_worker_get_the_egress_network(self) -> None:
        with_egress = {
            name
            for name, service in OVERRIDE_COMPOSE["services"].items()
            if EGRESS in service.get("networks", [])
        }

        assert with_egress == STRIPE_SERVICES

    def test_the_egress_network_has_a_fixed_subnet_that_cannot_hit_the_vpn_or_the_lan(self) -> None:
        subnet = ipaddress.ip_network(
            OVERRIDE_COMPOSE["networks"][EGRESS]["ipam"]["config"][0]["subnet"]
        )
        forbidden = [
            ipaddress.ip_network("192.168.0.0/16"),  # VPN et LAN de l'hôte
            ipaddress.ip_network("172.16.0.0/12"),  # réserve de Docker
            ipaddress.ip_network("10.250.0.0/24"),  # mail-egress
            ipaddress.ip_network("10.251.0.0/24"),  # payment-hook
        ]

        assert not any(subnet.overlaps(other) for other in forbidden)

    def test_the_override_declares_no_other_network_and_does_not_touch_the_gateway(self) -> None:
        assert set(OVERRIDE_COMPOSE["networks"]) == {EGRESS}
        assert set(OVERRIDE_COMPOSE["services"]) == {"shop", "payment", "payment-worker"}
        for service in OVERRIDE_COMPOSE["services"].values():
            assert "ports" not in service

    def test_payment_and_its_worker_switch_to_the_stripe_provider_with_a_key_file(self) -> None:
        for name in STRIPE_SERVICES:
            env = OVERRIDE_COMPOSE["services"][name]["environment"]

            assert env["APP_PAYMENT_PROVIDER"] == "stripe"
            assert env["APP_STRIPE_SECRET_KEY_FILE"] == "/run/secrets/payment_stripe_secret_key"
            assert "payment_stripe_secret_key" in OVERRIDE_COMPOSE["services"][name]["secrets"]

    def test_only_payment_reads_the_webhook_secret(self) -> None:
        services = OVERRIDE_COMPOSE["services"]

        assert "APP_STRIPE_WEBHOOK_SECRET_FILE" in services["payment"]["environment"]
        assert "payment_stripe_webhook_secret" in services["payment"]["secrets"]
        assert "APP_STRIPE_WEBHOOK_SECRET_FILE" not in services["payment-worker"]["environment"]
        assert "payment_stripe_webhook_secret" not in services["payment-worker"]["secrets"]

    def test_the_return_urls_are_required_and_the_checkout_flag_is_on_for_shop(self) -> None:
        env = OVERRIDE_COMPOSE["services"]["payment"]["environment"]

        assert env["APP_STRIPE_SUCCESS_URL"].startswith("${STRIPE_SUCCESS_URL:?")
        assert env["APP_STRIPE_CANCEL_URL"].startswith("${STRIPE_CANCEL_URL:?")
        shop = OVERRIDE_COMPOSE["services"]["shop"]["environment"]
        assert shop["APP_STRIPE_CHECKOUT_ENABLED"] == "true"

    def test_the_secrets_are_files_under_the_secrets_directory(self) -> None:
        for name, secret in OVERRIDE_COMPOSE["secrets"].items():
            assert set(secret) == {"file"}
            assert secret["file"].startswith("${SECRETS_DIR:-/opt/basecamp/secrets}/payment/")
            assert name.startswith("payment_stripe_")

    def test_the_file_holds_no_secret_value(self) -> None:
        text = OVERRIDE.read_text(encoding="utf-8")

        assert not re.search(r"\b(sk|rk|pk)_(test|live)_[A-Za-z0-9]{8,}", text)
        assert not re.search(r"whsec_[A-Za-z0-9]{8,}", text)


class TestEnvExample:
    def test_the_return_urls_are_documented_as_comments_so_the_default_install_is_unchanged(
        self,
    ) -> None:
        text = ENV_EXAMPLE.read_text(encoding="utf-8")

        assert re.search(r"^# STRIPE_SUCCESS_URL=https://", text, re.MULTILINE)
        assert re.search(r"^# STRIPE_CANCEL_URL=https://", text, re.MULTILINE)
        assert not re.search(r"^STRIPE_(SUCCESS|CANCEL)_URL=", text, re.MULTILINE)
