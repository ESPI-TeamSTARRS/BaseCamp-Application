from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StripeEvent:
    """Événement Stripe authentifié : identifiant, type et `data.object` tels que reçus."""

    id: str
    type: str
    object: Mapping[str, Any]
