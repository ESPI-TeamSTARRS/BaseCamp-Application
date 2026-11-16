from dataclasses import dataclass


@dataclass(frozen=True)
class CheckoutSessionView:
    intent_id: str
    session_id: str
    url: str
    created: bool
