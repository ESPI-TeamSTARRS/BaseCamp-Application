from dataclasses import dataclass


@dataclass(frozen=True)
class CreatedCheckoutSession:
    session_id: str
    url: str
