from dataclasses import dataclass


@dataclass(frozen=True)
class CreateCheckoutSessionCommand:
    caller: str
    intent_id: str
