import base64
import json
import os
from typing import Any

from openai import OpenAI  # type: ignore

from app.config import (
    ACTIVATION_CONVERSATION_PROMPT_PATH,
    IMAGE_ORDER_EXTRACTION_PROMPT_PATH,
)
from app.models import ActivationConversationResult, ImageOrderInfo
from app.services.AI.base import AIService
from app.services.image_extraction_service import ImageExtractionService


class OpenAIProvider(AIService):
    provider_name = "openai"

    def __init__(self) -> None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("Thiếu OPENAI_API_KEY trong file .env")

        self.client = OpenAI(api_key=api_key)
        self.model = os.getenv("OPENAI_MODEL", "gpt-5.6-sol").strip()
        if not self.model:
            raise RuntimeError("OPENAI_MODEL không được để trống")

        if not IMAGE_ORDER_EXTRACTION_PROMPT_PATH.exists():
            raise RuntimeError(
                f"Không tìm thấy prompt đọc ảnh: "
                f"{IMAGE_ORDER_EXTRACTION_PROMPT_PATH}"
            )
        self.image_prompt = IMAGE_ORDER_EXTRACTION_PROMPT_PATH.read_text(
            encoding="utf-8"
        ).strip()
        self.activation_prompt = ACTIVATION_CONVERSATION_PROMPT_PATH.read_text(
            encoding="utf-8"
        ).strip()
        self.image_extraction_service = ImageExtractionService()

    def activation_conversation(
        self,
        event: str,
        context: dict[str, Any],
        customer_message: str | None = None,
    ) -> dict[str, str]:
        response = self.client.responses.parse(
            model=self.model,
            instructions=self.activation_prompt,
            input=json.dumps(
                {
                    "event": event,
                    "context": context,
                    "customer_message": customer_message,
                },
                ensure_ascii=False,
                default=str,
            ),
            text_format=ActivationConversationResult,
        )
        if response.output_parsed is None:
            raise RuntimeError("OpenAI không trả kết quả hội thoại")
        return response.output_parsed.model_dump()

    def extract_order_from_image(
        self,
        image_bytes: bytes,
        mime_type: str,
    ) -> dict[str, Any]:
        self.image_extraction_service.validate_input(image_bytes, mime_type)
        encoded_image = base64.b64encode(image_bytes).decode("ascii")
        response = self.client.responses.parse(
            model=self.model,
            instructions=self.image_prompt,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Đọc mã đơn trong ảnh."},
                        {
                            "type": "input_image",
                            "image_url": f"data:{mime_type};base64,{encoded_image}",
                            "detail": "high",
                        },
                    ],
                }
            ],
            text_format=ImageOrderInfo,
        )
        if response.output_parsed is None:
            raise RuntimeError("OpenAI không trả kết quả đọc ảnh")
        return self.image_extraction_service.normalize(response.output_parsed)
