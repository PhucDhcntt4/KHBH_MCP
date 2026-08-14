import json
import os
from typing import Any

from google import genai
from google.genai import types  # type: ignore

from app.config import IMAGE_ORDER_EXTRACTION_PROMPT_PATH
from app.config import ACTIVATION_CONVERSATION_PROMPT_PATH
from app.models import ActivationConversationResult, ImageOrderInfo
from app.services.AI.base import AIService
from app.services.image_extraction_service import ImageExtractionService


class GeminiProvider(AIService):
    provider_name = "gemini"

    def __init__(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("Thiếu GEMINI_API_KEY trong file .env")

        self.client = genai.Client(api_key=api_key)
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
        if not self.model:
            raise RuntimeError("GEMINI_MODEL không được để trống")

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
        payload = {
            "event": event,
            "context": context,
            "customer_message": customer_message,
        }
        response = self.client.models.generate_content(
            model=self.model,
            contents=json.dumps(payload, ensure_ascii=False, default=str),
            config=types.GenerateContentConfig(
                system_instruction=self.activation_prompt,
                response_mime_type="application/json",
                response_schema=ActivationConversationResult,
                temperature=0,
            ),
        )
        if not response.text:
            raise RuntimeError("Gemini không trả kết quả hội thoại")
        result = ActivationConversationResult.model_validate_json(response.text)
        return result.model_dump()

    def extract_order_from_image(
        self,
        image_bytes: bytes,
        mime_type: str,
    ) -> dict[str, Any]:
        self.image_extraction_service.validate_input(image_bytes, mime_type)
        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        response = self.client.models.generate_content(
            model=self.model,
            contents=[self.image_prompt, image_part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ImageOrderInfo,
                temperature=0,
            ),
        )
        if not response.text:
            raise RuntimeError("Gemini không trả kết quả đọc ảnh")
        extracted = ImageOrderInfo.model_validate(json.loads(response.text))
        return self.image_extraction_service.normalize(extracted)
