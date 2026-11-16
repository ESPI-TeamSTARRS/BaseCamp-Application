"""Lecture d'un événement Stripe authentifié (WORK-89) : identifiant, type et objet, rien d'autre."""

import json
from typing import Any

import pytest

from app.stripe_webhook.domain.errors.invalid_stripe_payload_error import (
    InvalidStripePayloadError,
)
from app.stripe_webhook.domain.services.stripe_event_parser import StripeEventParser


def _body(**overrides: Any) -> bytes:
    payload: dict[str, Any] = {
        "id": "evt_1Abc",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_test_a1", "amount_total": 4990}},
    }
    payload.update(overrides)
    return json.dumps(payload).encode()


class TestParse:
    def test_a_valid_event_is_read(self) -> None:
        event = StripeEventParser().parse(_body())

        assert (event.id, event.type) == ("evt_1Abc", "checkout.session.completed")
        assert event.object == {"id": "cs_test_a1", "amount_total": 4990}

    @pytest.mark.parametrize(
        ("body", "reason"),
        [
            (b"not json", "payload_not_json"),
            (b"\xff\xfe", "payload_not_json"),
            (b"[]", "payload_not_an_object"),
            (b'"texte"', "payload_not_an_object"),
            (_body(id=None), "event_id_invalid"),
            (_body(id="pas_un_evenement"), "event_id_invalid"),
            (_body(id="evt_" + "a" * 300), "event_id_invalid"),
            (_body(type=None), "event_type_invalid"),
            (_body(type="Checkout Session"), "event_type_invalid"),
            (_body(data=None), "event_object_missing"),
            (_body(data={}), "event_object_missing"),
            (_body(data={"object": "x"}), "event_object_missing"),
        ],
    )
    def test_anything_else_is_refused_with_a_fixed_reason(self, body: bytes, reason: str) -> None:
        with pytest.raises(InvalidStripePayloadError) as error:
            StripeEventParser().parse(body)

        assert error.value.reason == reason
