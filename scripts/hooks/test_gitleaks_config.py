"""`.gitleaks.toml` : les clés Stripe sont détectées, les placeholders non (WORK-86).

Les clés d'exemple sont assemblées à l'exécution : aucune chaîne qui ressemble à une clé n'est écrite ici.
"""

import re
import tomllib
from pathlib import Path

import pytest

CONFIG = Path(__file__).resolve().parents[2] / ".gitleaks.toml"
BODY = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


def _rules() -> dict[str, re.Pattern[str]]:
    document = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    return {rule["id"]: re.compile(rule["regex"]) for rule in document.get("rules", [])}


def test_the_default_rules_stay_enabled() -> None:
    document = tomllib.loads(CONFIG.read_text(encoding="utf-8"))

    assert document["extend"]["useDefault"] is True


@pytest.mark.parametrize(
    ("rule", "prefix"),
    [
        ("stripe-publishable-key", "pk_" + "test_"),
        ("stripe-publishable-key", "pk_" + "live_"),
        ("stripe-webhook-secret", "whsec_"),
    ],
)
def test_a_key_of_each_custom_kind_is_detected(rule: str, prefix: str) -> None:
    assert _rules()[rule].search(f'STRIPE="{prefix}{BODY}"')


@pytest.mark.parametrize(
    "text",
    [
        "pk_" + "test_court",
        "whsec_",
        "whsec_" + "test_test_test_test_test_test",
        "la cle commence par whsec_ dans la doc",
        "risk_assessment_" + BODY,
    ],
)
def test_placeholders_and_prose_are_not_flagged(text: str) -> None:
    assert not any(pattern.search(text) for pattern in _rules().values())
