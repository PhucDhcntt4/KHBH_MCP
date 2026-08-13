from typing import Literal
from pydantic import BaseModel, Field # type: ignore

class ChatHistoryItem(BaseModel):
    role: Literal["user","model"]
    text: str = Field(
        ...,
        min_length=1,
    )

class WarrantyMessageRequest(BaseModel):
    customer_id: str = Field(
        ...,
        min_length=1,
        description=(
            "Mã người dùng từ Facebook, Zalo hoặc website"
        ),
    )

    message: str = Field(
        ...,
        min_length=1,
        description="Tin nhắn hiện tại của khách hàng",
    )

    history: list[ChatHistoryItem] = Field(
        default_factory=list,
        description="Lịch sử hội thoại trước tin nhắn hiện tại",
    )

class WarrantyMessageResponse(BaseModel):
    status: str
    message: str

class ImageOrderInfo(BaseModel):
    phone: str | None = None
    masked_phone: str | None = None
    order_code: str | None = None
    phone_confident: bool = False
    masked_phone_confident: bool = False
    order_code_confident: bool = False


class ConfirmationIntent(BaseModel):
    intent: Literal["confirm", "cancel", "unknown"]
