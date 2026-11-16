from pathlib import Path

import pytest

from app.confirmation.domain.enums.payment_method import PaymentMethod
from app.confirmation.domain.exceptions.intent_not_confirmable_error import (
    IntentNotConfirmableError,
)
from app.confirmation.infrastructure.providers.internal_payment_provider import (
    InternalPaymentProvider,
)
from app.confirmation.infrastructure.providers.stripe_hosted_payment_provider import (
    StripeHostedPaymentProvider,
)
from app.confirmation.wiring import build_payment_provider
from app.core.config import Settings
from app.intent.domain.value_objects.amount import Amount

TEST_KEY = "sk_" + "test_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


def _never_wait(_: float) -> None:
    raise AssertionError("aucune attente attendue")


class TestBuildPaymentProvider:
    def test_the_simulated_provider_is_the_internal_one(self) -> None:
        provider = build_payment_provider(Settings(), _never_wait)

        assert isinstance(provider, InternalPaymentProvider)

    def test_the_stripe_provider_never_authorizes_a_synchronous_payment(
        self, tmp_path: Path
    ) -> None:
        key_file = tmp_path / "key"
        key_file.write_text(TEST_KEY, encoding="utf-8")
        settings = Settings(payment_provider="stripe", stripe_secret_key_file=str(key_file))

        provider = build_payment_provider(settings, _never_wait)

        assert isinstance(provider, StripeHostedPaymentProvider)
        with pytest.raises(IntentNotConfirmableError):
            provider.authorize(PaymentMethod.SUCCESS, Amount(4990))
