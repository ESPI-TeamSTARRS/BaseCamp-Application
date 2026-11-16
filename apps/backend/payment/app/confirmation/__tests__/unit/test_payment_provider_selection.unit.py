from pathlib import Path

import pytest

from app.confirmation.domain.exceptions.payment_provider_unavailable_error import (
    PaymentProviderUnavailableError,
)
from app.confirmation.infrastructure.providers.internal_payment_provider import (
    InternalPaymentProvider,
)
from app.confirmation.wiring import build_payment_provider
from app.core.config import Settings

TEST_KEY = "sk_" + "test_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


def _never_wait(_: float) -> None:
    raise AssertionError("aucune attente attendue")


class TestBuildPaymentProvider:
    def test_the_simulated_provider_is_the_internal_one(self) -> None:
        provider = build_payment_provider(Settings(), _never_wait)

        assert isinstance(provider, InternalPaymentProvider)

    def test_the_stripe_provider_is_refused_until_its_adapter_exists(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text(TEST_KEY, encoding="utf-8")
        settings = Settings(payment_provider="stripe", stripe_secret_key_file=str(key_file))

        with pytest.raises(PaymentProviderUnavailableError) as error:
            build_payment_provider(settings, _never_wait)

        assert "stripe" in str(error.value)
        assert TEST_KEY not in str(error.value)
