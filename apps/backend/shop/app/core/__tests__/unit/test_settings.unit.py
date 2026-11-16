import pytest

from app.core.config import Settings


class TestSettingsDatabaseUrl:
    def test_database_url_is_built_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_DB_HOST", "postgres")
        monkeypatch.setenv("APP_DB_PORT", "5433")
        monkeypatch.setenv("APP_DB_NAME", "shop_db")
        monkeypatch.setenv("APP_DB_USER", "shop_user")
        monkeypatch.setenv("APP_DB_PASSWORD", "p@ss/word")

        url = Settings().database_url

        assert url == "postgresql+psycopg://shop_user:p%40ss%2Fword@postgres:5433/shop_db"

    def test_database_url_has_local_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("HOST", "PORT", "NAME", "USER", "PASSWORD"):
            monkeypatch.delenv(f"APP_DB_{name}", raising=False)

        url = Settings().database_url

        assert url.startswith("postgresql+psycopg://shop:")
        assert url.endswith("@localhost:5432/shop")


class TestSettingsLogPseudonymKey:
    def test_key_is_optional_in_dev_and_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("APP_LOG_PSEUDONYM_KEY", raising=False)
        for environment in ("dev", "local"):
            monkeypatch.setenv("APP_ENVIRONMENT", environment)

            assert Settings().log_pseudonym_key == ""

    @pytest.mark.parametrize("environment", ["staging", "production"])
    def test_startup_fails_outside_dev_without_the_key(
        self, monkeypatch: pytest.MonkeyPatch, environment: str
    ) -> None:
        monkeypatch.delenv("APP_LOG_PSEUDONYM_KEY", raising=False)
        monkeypatch.setenv("APP_ENVIRONMENT", environment)

        with pytest.raises(ValueError, match="APP_LOG_PSEUDONYM_KEY"):
            Settings()

    def test_key_is_read_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENVIRONMENT", "production")
        monkeypatch.setenv("APP_LOG_PSEUDONYM_KEY", "une-cle-longue-et-secrete")

        assert Settings().log_pseudonym_key == "une-cle-longue-et-secrete"


class TestStripeCheckoutSettings:
    def test_stripe_checkout_is_off_by_default_with_a_longer_checkout_timeout(self) -> None:
        settings = Settings()

        assert settings.stripe_checkout_enabled is False
        assert (
            settings.payment_checkout_read_timeout_seconds > settings.payment_read_timeout_seconds
        )

    def test_it_is_enabled_by_the_environment_with_the_payment_service(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APP_STRIPE_CHECKOUT_ENABLED", "true")

        assert Settings().stripe_checkout_enabled is True

    def test_it_cannot_be_enabled_with_the_simulated_provider(self) -> None:
        with pytest.raises(ValueError, match="APP_PAYMENT_PROVIDER=http"):
            Settings(stripe_checkout_enabled=True, payment_provider="simulated")

    @pytest.mark.parametrize("value", [0, -1, 31])
    def test_the_checkout_timeout_is_bounded(self, value: float) -> None:
        with pytest.raises(ValueError, match="payment_checkout_read_timeout_seconds"):
            Settings(payment_checkout_read_timeout_seconds=value)
