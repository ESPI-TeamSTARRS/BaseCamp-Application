"""Clés Stripe lues dans des fichiers (secrets Docker) et validées avant tout démarrage (WORK-86).

Mode test uniquement : seule une clé secrète `sk_test_…` est acceptée, une clé live (ou n'importe quoi
d'autre) est refusée au démarrage. Les messages d'erreur sont fixes : ni la valeur lue ni le chemin du
fichier n'y figurent, pour que rien de sensible n'atteigne les journaux ou une trace d'exception.
"""

import re
from pathlib import Path

from pydantic import SecretStr

MAX_SECRET_FILE_BYTES = (
    512  # une clé Stripe fait une centaine de caractères : au-delà, ce n'est pas une clé
)
_SECRET_KEY = re.compile(r"sk_test_[A-Za-z0-9]{16,}")
_WEBHOOK_SECRET = re.compile(r"whsec_[A-Za-z0-9]{16,}")


class StripeCredentialsError(ValueError):
    """Configuration Stripe refusée (message fixe, jamais la valeur ni le chemin)."""


def _read(path: str) -> str:
    try:
        file = Path(path)
        if not file.is_file():
            raise StripeCredentialsError("the Stripe secret file is unreadable")
        if file.stat().st_size > MAX_SECRET_FILE_BYTES:
            raise StripeCredentialsError("the Stripe secret file is too large")
        return file.read_text(encoding="utf-8").strip()
    except StripeCredentialsError:
        raise
    except (OSError, UnicodeDecodeError):
        raise StripeCredentialsError("the Stripe secret file is unreadable") from None


def load_secret_key(path: str) -> SecretStr:
    """Clé secrète de test lue dans son fichier; refuse une clé live ou mal formée."""
    if not path:
        raise StripeCredentialsError(
            "APP_STRIPE_SECRET_KEY_FILE is required when APP_PAYMENT_PROVIDER=stripe"
        )
    value = _read(path)
    if not value:
        raise StripeCredentialsError("the Stripe secret key file is empty")
    if not _SECRET_KEY.fullmatch(value):
        raise StripeCredentialsError("the Stripe secret key must be a test key (sk_test_...)")
    return SecretStr(value)


def load_webhook_secret(path: str) -> SecretStr:
    """Secret de signature des webhooks Stripe (`whsec_…`) lu dans son fichier."""
    value = _read(path)
    if not _WEBHOOK_SECRET.fullmatch(value):
        raise StripeCredentialsError("the Stripe webhook secret must start with whsec_")
    return SecretStr(value)
