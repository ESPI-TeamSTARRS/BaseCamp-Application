"""Configuration Stripe (WORK-86) : fournisseur choisi par l'environnement, clés lues dans des fichiers.

Les fausses clés sont assemblées à l'exécution (préfixe + corps) : aucune chaîne qui ressemble à une clé
n'est écrite dans le dépôt, et les valeurs de test ne sont pas de vraies clés.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

BODY = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
TEST_KEY = "sk_" + "test_" + BODY
LIVE_KEY = "sk_" + "live_" + BODY
WEBHOOK_SECRET = "whsec_" + BODY


def _file(directory: Path, content: str, name: str = "key") -> str:
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def _stripe(key_file: str, **extra: object) -> Settings:
    return Settings(payment_provider="stripe", stripe_secret_key_file=key_file, **extra)  # type: ignore[arg-type]


class TestProviderSelection:
    def test_the_simulated_provider_is_the_default_and_needs_no_key(self) -> None:
        settings = Settings()

        assert settings.payment_provider == "simulated"
        assert settings.stripe_secret_key is None

    def test_the_environment_selects_the_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_PAYMENT_PROVIDER", "simulated")

        assert Settings().payment_provider == "simulated"

    @pytest.mark.parametrize("value", ["Stripe", "paypal", "", "live"])
    def test_an_unknown_provider_is_refused(self, value: str) -> None:
        with pytest.raises(ValidationError):
            Settings(payment_provider=value)  # type: ignore[arg-type]

    def test_a_key_file_is_ignored_while_the_simulated_provider_is_selected(
        self, tmp_path: Path
    ) -> None:
        settings = Settings(stripe_secret_key_file=_file(tmp_path, "pas-une-cle"))

        assert settings.stripe_secret_key is None


class TestStripeSecretKey:
    def test_a_test_key_is_read_from_its_file_and_stripped(self, tmp_path: Path) -> None:
        settings = _stripe(_file(tmp_path, TEST_KEY + "\n"))

        assert settings.stripe_secret_key is not None
        assert settings.stripe_secret_key.get_secret_value() == TEST_KEY

    def test_the_key_never_shows_in_the_representation_of_the_settings(
        self, tmp_path: Path
    ) -> None:
        settings = _stripe(_file(tmp_path, TEST_KEY))

        assert TEST_KEY not in repr(settings) and TEST_KEY not in str(settings)
        assert BODY not in repr(settings.stripe_secret_key)

    def test_the_stripe_provider_without_a_key_file_refuses_to_start(self) -> None:
        with pytest.raises(ValidationError, match="APP_STRIPE_SECRET_KEY_FILE"):
            Settings(payment_provider="stripe")

    @pytest.mark.parametrize(
        ("content", "reason"),
        [
            (LIVE_KEY, "test key"),
            ("rk_" + "test_" + BODY, "test key"),
            ("pk_" + "test_" + BODY, "test key"),
            ("sk_" + "test_court", "test key"),
            ("n'importe quoi", "test key"),
            ("", "empty"),
            ("   \n", "empty"),
        ],
    )
    def test_anything_but_a_test_secret_key_is_refused_without_echoing_it(
        self, tmp_path: Path, content: str, reason: str
    ) -> None:
        with pytest.raises(ValidationError) as error:
            _stripe(_file(tmp_path, content))

        message = str(error.value)
        assert reason in message
        assert BODY not in message
        assert content.strip() == "" or content.strip() not in message

    def test_an_unreadable_key_file_is_refused_with_a_generic_message(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="unreadable") as error:
            _stripe(str(tmp_path / "absent"))

        assert str(tmp_path) not in str(error.value)

    def test_a_directory_instead_of_a_file_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="unreadable"):
            _stripe(str(tmp_path))

    def test_a_huge_file_is_refused_before_being_read_whole(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="too large"):
            _stripe(_file(tmp_path, TEST_KEY + "x" * 10_000))


class TestStripeWebhookSecret:
    def test_it_is_optional_for_now(self, tmp_path: Path) -> None:
        settings = _stripe(_file(tmp_path, TEST_KEY))

        assert settings.stripe_webhook_secret is None

    def test_a_valid_webhook_secret_is_read_from_its_file(self, tmp_path: Path) -> None:
        settings = _stripe(
            _file(tmp_path, TEST_KEY),
            stripe_webhook_secret_file=_file(tmp_path, WEBHOOK_SECRET + "\n", "webhook"),
        )

        assert settings.stripe_webhook_secret is not None
        assert settings.stripe_webhook_secret.get_secret_value() == WEBHOOK_SECRET
        assert WEBHOOK_SECRET not in repr(settings)

    def test_a_malformed_webhook_secret_is_refused_without_echoing_it(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="whsec_") as error:
            _stripe(
                _file(tmp_path, TEST_KEY),
                stripe_webhook_secret_file=_file(tmp_path, "sk_" + "test_" + BODY, "webhook"),
            )

        assert BODY not in str(error.value)


class TestStripeWebhookSettings:
    def test_the_webhook_secret_is_read_even_while_the_simulated_provider_is_selected(
        self, tmp_path: Path
    ) -> None:
        settings = Settings(stripe_webhook_secret_file=_file(tmp_path, WEBHOOK_SECRET, "webhook"))

        assert settings.payment_provider == "simulated"
        assert settings.stripe_webhook_secret is not None
        assert settings.stripe_webhook_secret.get_secret_value() == WEBHOOK_SECRET

    def test_a_malformed_webhook_secret_stops_the_startup_whatever_the_provider(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(ValidationError, match="whsec_"):
            Settings(stripe_webhook_secret_file=_file(tmp_path, "pas-un-secret", "webhook"))

    def test_the_webhook_has_a_bounded_tolerance_and_its_own_body_limit(self) -> None:
        settings = Settings()

        assert settings.stripe_webhook_tolerance_seconds == 300
        assert settings.stripe_webhook_max_body_bytes == 65_536
        assert settings.stripe_webhook_max_body_bytes > settings.max_body_bytes

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("stripe_webhook_tolerance_seconds", 0),
            ("stripe_webhook_tolerance_seconds", 3601),
            ("stripe_webhook_max_body_bytes", 1023),
            ("stripe_webhook_max_body_bytes", 1_048_577),
        ],
    )
    def test_the_bounds_are_enforced(self, name: str, value: int) -> None:
        with pytest.raises(ValidationError):
            Settings(**{name: value})  # type: ignore[arg-type]
