"""`scripts/init-stripe-secrets.sh` : saisie des clés Stripe de test dans des fichiers (WORK-86).

Les fausses clés sont assemblées à l'exécution : aucune chaîne qui ressemble à une clé n'est écrite ici.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "init-stripe-secrets.sh"
BODY = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
TEST_KEY = "sk_" + "test_" + BODY
LIVE_KEY = "sk_" + "live_" + BODY
WEBHOOK = "whsec_" + BODY

pytestmark = pytest.mark.skipif(not shutil.which("sh"), reason="sh requis")


def _run(
    directory: Path, stdin: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "STRIPE_SECRETS_DIR": str(directory)}
    return subprocess.run(  # noqa: S603 - script du dépôt, arguments de test
        ["sh", str(SCRIPT), *arguments],  # noqa: S607
        input=stdin,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
        timeout=30,
    )


def test_a_test_key_is_written_to_its_file_and_never_printed(tmp_path: Path) -> None:
    result = _run(tmp_path, TEST_KEY + "\n")

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "stripe-secret-key").read_text(encoding="utf-8").strip() == TEST_KEY
    assert BODY not in result.stdout + result.stderr


@pytest.mark.skipif(os.name == "nt", reason="droits POSIX")
def test_the_file_is_readable_by_the_container_user_but_not_writable(tmp_path: Path) -> None:
    _run(tmp_path, TEST_KEY + "\n")

    assert oct((tmp_path / "stripe-secret-key").stat().st_mode & 0o777) == "0o644"


@pytest.mark.parametrize("value", [LIVE_KEY, "rk_" + "test_" + BODY, "n'importe quoi", "", "sk_" + "test_court"])
def test_anything_but_a_test_secret_key_is_refused_and_nothing_is_written(
    tmp_path: Path, value: str
) -> None:
    result = _run(tmp_path, value + "\n")

    assert result.returncode != 0
    assert not (tmp_path / "stripe-secret-key").exists()
    assert BODY not in result.stdout + result.stderr


def test_an_existing_key_is_never_replaced_without_force(tmp_path: Path) -> None:
    _run(tmp_path, TEST_KEY + "\n")
    other = "sk_" + "test_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6"

    refused = _run(tmp_path, other + "\n")
    replaced = _run(tmp_path, other + "\n", "--force")

    assert refused.returncode != 0
    assert replaced.returncode == 0
    assert (tmp_path / "stripe-secret-key").read_text(encoding="utf-8").strip() == other


def test_the_webhook_secret_goes_to_its_own_file_and_must_start_with_whsec(tmp_path: Path) -> None:
    refused = _run(tmp_path, TEST_KEY + "\n", "--webhook")
    accepted = _run(tmp_path, WEBHOOK + "\n", "--webhook")

    assert refused.returncode != 0
    assert accepted.returncode == 0, accepted.stderr
    assert (tmp_path / "stripe-webhook-secret").read_text(encoding="utf-8").strip() == WEBHOOK
    assert not (tmp_path / "stripe-secret-key").exists()
    assert BODY not in accepted.stdout + accepted.stderr
