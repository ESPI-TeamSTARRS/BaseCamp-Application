"""Plages réseau de la VM (docs/deployment/runbook.md, « Plages réseau de la VM »).

Une plage fixée dans un compose qui chevauche une plage déjà prise sur la VM empêche la création du réseau
et fait échouer le déploiement (incident du 17/11 avec `payment-hook`). Ces tests ne lancent aucun conteneur :
ils obligent toute plage de la pile de production à figurer dans le tableau du runbook, sans chevauchement.
"""

import ipaddress
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs" / "deployment" / "runbook.md"
COMPOSE_FILES = [
    ROOT / "deploy" / "docker-compose.prod.yml",
    ROOT / "deploy" / "docker-compose.prod.stripe.yml",
]
ROW = re.compile(r"^\| `(?P<range>[0-9.]+/\d+)` \| (?P<usage>.+) \|$", re.MULTILINE)


def _reserved() -> dict[str, str]:
    text = RUNBOOK.read_text(encoding="utf-8")
    section = text.split("## Plages réseau de la VM", 1)[1].split("\n## ", 1)[0]
    return {match["range"]: match["usage"] for match in ROW.finditer(section)}


def _compose_subnets() -> dict[str, str]:
    """Nom du réseau -> plage fixée, pour chaque réseau qui en déclare une."""
    found: dict[str, str] = {}
    for path in COMPOSE_FILES:
        document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, network in (document.get("networks") or {}).items():
            for config in ((network or {}).get("ipam") or {}).get("config", []):
                found[name] = config["subnet"]
    return found


def test_the_runbook_lists_the_reserved_ranges() -> None:
    reserved = _reserved()

    assert {"10.250.0.0/24", "10.251.0.0/24", "10.252.0.0/24", "10.253.0.0/24"} <= set(reserved)
    assert "192.168.1.0/24" in reserved and "172.16.0.0/12" in reserved


def test_no_two_reserved_ranges_overlap() -> None:
    networks = [(name, ipaddress.ip_network(name)) for name in _reserved()]

    for index, (first_name, first) in enumerate(networks):
        for second_name, second in networks[index + 1 :]:
            assert not first.overlaps(second), f"{first_name} chevauche {second_name}"


def test_every_fixed_subnet_of_the_production_stack_is_in_the_table_under_its_network_name() -> None:
    reserved = _reserved()
    subnets = _compose_subnets()

    assert subnets, "aucune plage fixe trouvée : le test ne vérifie plus rien"
    for network, subnet in subnets.items():
        assert subnet in reserved, f"{network} : {subnet} absente du tableau du runbook"
        assert f"`{network}`" in reserved[subnet], f"{subnet} n'est pas attribuée à {network}"


def test_a_stack_subnet_never_uses_a_range_marked_as_not_to_use() -> None:
    reserved = _reserved()
    for network, subnet in _compose_subnets().items():
        usage = reserved[subnet]
        assert "ne pas utiliser" not in usage, f"{network} utilise une plage interdite : {subnet}"
