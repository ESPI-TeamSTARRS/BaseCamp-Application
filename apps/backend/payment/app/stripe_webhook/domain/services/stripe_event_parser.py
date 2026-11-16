import json
import re
from typing import Any

from app.stripe_webhook.domain.errors.invalid_stripe_payload_error import (
    InvalidStripePayloadError,
)
from app.stripe_webhook.domain.value_objects.stripe_event import StripeEvent

_EVENT_ID = re.compile(r"evt_[A-Za-z0-9_]{1,200}")
_EVENT_TYPE = re.compile(r"[a-z0-9_.]{1,100}")


class StripeEventParser:
    """Lit le corps (déjà authentifié par sa signature) : identifiant, type et objet, rien d'autre."""

    def parse(self, raw_body: bytes) -> StripeEvent:
        try:
            payload: Any = json.loads(raw_body)
        except ValueError:
            raise InvalidStripePayloadError("payload_not_json") from None
        if not isinstance(payload, dict):
            raise InvalidStripePayloadError("payload_not_an_object")
        event_id, event_type = payload.get("id"), payload.get("type")
        if not isinstance(event_id, str) or not _EVENT_ID.fullmatch(event_id):
            raise InvalidStripePayloadError("event_id_invalid")
        if not isinstance(event_type, str) or not _EVENT_TYPE.fullmatch(event_type):
            raise InvalidStripePayloadError("event_type_invalid")
        data = payload.get("data")
        event_object = data.get("object") if isinstance(data, dict) else None
        if not isinstance(event_object, dict):
            raise InvalidStripePayloadError("event_object_missing")
        return StripeEvent(event_id, event_type, event_object)
