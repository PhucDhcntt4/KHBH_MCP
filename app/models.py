from typing import Literal

from pydantic import BaseModel  # type: ignore

class ImageOrderInfo(BaseModel):
    phone: str | None = None
    masked_phone: str | None = None
    order_code: str | None = None
    phone_confident: bool = False
    masked_phone_confident: bool = False
    order_code_confident: bool = False


class ActivationConversationResult(BaseModel):
    intent: Literal["confirm", "cancel", "unknown"] = "unknown"
    reply: str
