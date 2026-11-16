from pydantic import BaseModel


class CheckoutSessionResponse(BaseModel):
    intent_id: str
    session_id: str
    checkout_url: str
