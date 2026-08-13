import json
import os
from typing import Any

from google import genai
from google.genai import types # type: ignore

from app.config import (
    CONFIRMATION_PROMPT_PATH,
    IMAGE_ORDER_EXTRACTION_PROMPT_PATH,
    WARRANTY_PROMPT_PATH,
)
from app.models import (
    ConfirmationIntent,
    ImageOrderInfo,
)
from app.services.AI.base import AIService
from app.services.image_extraction_service import (
    ImageExtractionService,
)
from app.services.warranty_tools import (
    search_order,
    search_order_by_code,
    search_warranty_policy,
)


class GeminiProvider(AIService):
    provider_name = "gemini"

    def __init__(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise RuntimeError(
                "Thiếu GEMINI_API_KEY trong file .env"
            )

        self.client = genai.Client(
            api_key=api_key
        )

        self.model = os.getenv(
            "GEMINI_MODEL",
            "gemini-2.5-flash",
        )

        if not WARRANTY_PROMPT_PATH.exists():
            raise RuntimeError(
                f"Không tìm thấy prompt: {WARRANTY_PROMPT_PATH}"
            )

        self.system_prompt = WARRANTY_PROMPT_PATH.read_text(
            encoding="utf-8"
        )

        if not CONFIRMATION_PROMPT_PATH.exists():
            raise RuntimeError(
                "Không tìm thấy prompt phân loại xác nhận: "
                f"{CONFIRMATION_PROMPT_PATH}"
            )

        self.confirmation_prompt = (
            CONFIRMATION_PROMPT_PATH.read_text(
                encoding="utf-8"
            )
        )

        self.tools = [
            search_warranty_policy,
            search_order,
            search_order_by_code,
        ]
        if not IMAGE_ORDER_EXTRACTION_PROMPT_PATH.exists():
            raise RuntimeError(
                "Không tìm thấy prompt đọc thông tin đơn hàng: "
                f"{IMAGE_ORDER_EXTRACTION_PROMPT_PATH}"
            )

        self.image_prompt = (
            IMAGE_ORDER_EXTRACTION_PROMPT_PATH.read_text(
                encoding="utf-8"
            ).strip()
        )

        if not self.image_prompt:
            raise RuntimeError(
                "Prompt đọc thông tin đơn hàng đang để trống"
            )

        self.image_extraction_service = (
            ImageExtractionService()
        )

    def chat(
        self,
        message: str,
        customer_id: str,
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Gửi tin nhắn cho Gemini Agent.

        Gemini tự:
        - Hiểu yêu cầu
        - Chọn tool
        - Nhận kết quả tool
        - Viết câu trả lời cuối cùng
        """

        contents = self._build_contents(
            message=message,
            customer_id=customer_id,
            history=history,
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                tools=self.tools,
                temperature=0.2,
                automatic_function_calling=(
                    types.AutomaticFunctionCallingConfig(
                        disable=False,
                        maximum_remote_calls=5,
                    )
                ),
            ),
        )

        reply = response.text

        if not reply:
            reply = (
                "Dạ hiện tại em chưa thể xử lý yêu cầu. "
                "Anh/chị vui lòng thử lại sau ít phút ạ."
            )

        return {
            "success": True,
            "reply": reply.strip(),
        }

    def compose_reply(
        self,
        event: str,
        history: list[dict[str, Any]] | None = None,
    ) -> str:
        """
        Viết câu trả lời tự nhiên từ một kết quả nghiệp vụ đã được
        Python xác minh. Hàm này không đăng ký tools nên không thể
        tự tìm đơn hoặc kích hoạt lại.
        """

        contents: list[types.Content] = []

        if history:
            for item in history:
                role = item.get("role")
                text = item.get("text", "")

                if role in {"user", "model"} and text:
                    contents.append(
                        types.Content(
                            role=role,
                            parts=[
                                types.Part.from_text(text=text)
                            ],
                        )
                    )

        contents.append(
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=(
                            "KẾT QUẢ NGHIỆP VỤ ĐÃ ĐƯỢC HỆ THỐNG "
                            "XÁC MINH:\n"
                            f"{event}\n\n"
                            "Hãy viết một câu trả lời ngắn gọn, tự "
                            "nhiên bằng tiếng Việt cho khách hàng. "
                            "Chỉ dùng dữ liệu trên, không tự thêm "
                            "thông tin và không mô tả kỹ thuật."
                        )
                    )
                ],
            )
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                temperature=0.3,
            ),
        )

        if not response.text:
            raise RuntimeError("Gemini không tạo được câu trả lời")

        return response.text.strip()

    def classify_confirmation_intent(
        self,
        message: str,
    ) -> str:
        """
        Phân loại câu trả lời khi có yêu cầu kích hoạt đang chờ.
        Các cách diễn đạt được quản lý trong prompt, không nằm
        trong danh sách từ khóa Python.
        """

        response = self.client.models.generate_content(
            model=self.model,
            contents=message,
            config=types.GenerateContentConfig(
                system_instruction=self.confirmation_prompt,
                response_mime_type="application/json",
                temperature=0,
            ),
        )

        if not response.text:
            return "unknown"

        try:
            parsed = ConfirmationIntent.model_validate(
                json.loads(response.text)
            )
        except (json.JSONDecodeError, ValueError):
            return "unknown"

        return parsed.intent

    def extract_order_from_image(
        self,
        image_bytes: bytes,
        mime_type: str,
    ) -> dict[str, Any]:
        self.image_extraction_service.validate_input(
            image_bytes=image_bytes,
            mime_type=mime_type,
        )

        image_part = types.Part.from_bytes(
            data=image_bytes,
            mime_type=mime_type,
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=[
                self.image_prompt,
                image_part,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ImageOrderInfo,
                temperature=0,
            ),
        )

        if not response.text:
            raise RuntimeError(
                "Gemini không trả kết quả đọc ảnh"
            )

        extracted = ImageOrderInfo.model_validate(
            json.loads(response.text)
        )

        return self.image_extraction_service.normalize(
            extracted
        )

    def _build_contents(
        self,
        message: str,
        customer_id: str,
        history: list[dict[str, Any]] | None,
    ) -> list[types.Content]:
        """
        Chuyển lịch sử hội thoại và tin nhắn hiện tại
        thành định dạng Gemini Content.
        """

        contents: list[types.Content] = []

        if history:
            for item in history:
                role = item.get("role")
                text = item.get("text", "")

                if role not in {"user", "model"}:
                    continue

                if not text:
                    continue

                contents.append(
                    types.Content(
                        role=role,
                        parts=[
                            types.Part.from_text(
                                text=text
                            )
                        ],
                    )
                )

        current_message = (
            "THÔNG TIN HỆ THỐNG:\n"
            f"customer_id: {customer_id}\n\n"
            "TIN NHẮN KHÁCH HÀNG:\n"
            f"{message}"
        )

        contents.append(
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=current_message
                    )
                ],
            )
        )

        return contents
