import base64
import json
import os
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI # type: ignore

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


class OpenAIProvider(AIService):
    provider_name = "openai"

    def __init__(self) -> None:
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise RuntimeError(
                "Thiếu OPENAI_API_KEY trong file .env"
            )

        self.client = OpenAI(api_key=api_key)

        self.model = os.getenv(
            "OPENAI_MODEL",
            "gpt-5.6-sol",
        ).strip()

        if not self.model:
            raise RuntimeError(
                "OPENAI_MODEL không được để trống"
            )

        self.system_prompt = self._read_prompt(
            WARRANTY_PROMPT_PATH,
            "prompt bảo hành",
        )
        self.confirmation_prompt = self._read_prompt(
            CONFIRMATION_PROMPT_PATH,
            "prompt phân loại xác nhận",
        )
        self.image_prompt = self._read_prompt(
            IMAGE_ORDER_EXTRACTION_PROMPT_PATH,
            "prompt đọc ảnh đơn hàng",
        )

        self.image_extraction_service = (
            ImageExtractionService()
        )

        self.tool_functions: dict[
            str,
            Callable[..., dict[str, Any]],
        ] = {
            "search_warranty_policy": (
                search_warranty_policy
            ),
            "search_order": search_order,
            "search_order_by_code": search_order_by_code,
        }

        self.tools = self._build_tools()

    def chat(
        self,
        message: str,
        customer_id: str,
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        input_items = self._convert_history(history)

        input_items.append(
            {
                "role": "user",
                "content": (
                    "THÔNG TIN HỆ THỐNG:\n"
                    f"customer_id: {customer_id}\n\n"
                    "TIN NHẮN KHÁCH HÀNG:\n"
                    f"{message}"
                ),
            }
        )

        response = self._run_with_tools(input_items)
        reply = response.output_text

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
        input_items = self._convert_history(history)

        input_items.append(
            {
                "role": "user",
                "content": (
                    "KẾT QUẢ NGHIỆP VỤ ĐÃ ĐƯỢC HỆ THỐNG "
                    "XÁC MINH:\n"
                    f"{event}\n\n"
                    "Viết một câu trả lời ngắn gọn, tự nhiên "
                    "bằng tiếng Việt cho khách hàng. "
                    "Chỉ dùng dữ liệu trên, không tự thêm "
                    "thông tin và không mô tả kỹ thuật."
                ),
            }
        )

        response = self.client.responses.create(
            model=self.model,
            instructions=self.system_prompt,
            input=input_items,
        )

        if not response.output_text:
            raise RuntimeError(
                "OpenAI không tạo được câu trả lời"
            )

        return response.output_text.strip()

    def classify_confirmation_intent(
        self,
        message: str,
    ) -> str:
        try:
            response = self.client.responses.parse(
                model=self.model,
                instructions=self.confirmation_prompt,
                input=[
                    {
                        "role": "user",
                        "content": message,
                    }
                ],
                text_format=ConfirmationIntent,
            )
        except Exception:
            return "unknown"

        parsed = response.output_parsed

        if parsed is None:
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

        encoded_image = base64.b64encode(
            image_bytes
        ).decode("ascii")

        image_url = (
            f"data:{mime_type};base64,{encoded_image}"
        )

        response = self.client.responses.parse(
            model=self.model,
            instructions=self.image_prompt,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Đọc thông tin đơn hàng "
                                "trong ảnh này."
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": image_url,
                            "detail": "high",
                        },
                    ],
                }
            ],
            text_format=ImageOrderInfo,
        )

        extracted = response.output_parsed

        if extracted is None:
            raise RuntimeError(
                "OpenAI không trả kết quả đọc ảnh"
            )

        return self.image_extraction_service.normalize(
            extracted
        )

    def _run_with_tools(
        self,
        input_items: list[Any],
    ):
        for _ in range(5):
            response = self.client.responses.create(
                model=self.model,
                instructions=self.system_prompt,
                input=input_items,
                tools=self.tools,
            )

            # Giữ lại cả message và function call.
            input_items.extend(response.output)

            tool_calls = [
                item
                for item in response.output
                if item.type == "function_call"
            ]

            if not tool_calls:
                return response

            for tool_call in tool_calls:
                result = self._execute_tool(
                    name=tool_call.name,
                    arguments=tool_call.arguments,
                )

                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": tool_call.call_id,
                        "output": json.dumps(
                            result,
                            ensure_ascii=False,
                        ),
                    }
                )

        raise RuntimeError(
            "OpenAI vượt quá giới hạn 5 lượt gọi tool"
        )

    def _execute_tool(
        self,
        name: str,
        arguments: str,
    ) -> dict[str, Any]:
        function = self.tool_functions.get(name)

        if function is None:
            return {
                "success": False,
                "status": "unknown_tool",
                "message": "Công cụ không được hỗ trợ.",
            }

        try:
            parsed_arguments = json.loads(arguments)

            if not isinstance(parsed_arguments, dict):
                raise ValueError(
                    "Tool arguments phải là object"
                )

            return function(**parsed_arguments)

        except Exception as error:
            return {
                "success": False,
                "status": "tool_error",
                "message": str(error),
            }

    @staticmethod
    def _convert_history(
        history: list[dict[str, Any]] | None,
    ) -> list[Any]:
        input_items: list[Any] = []

        for item in history or []:
            role = item.get("role")
            text = item.get("text", "")

            if not text:
                continue

            if role == "model":
                role = "assistant"

            if role not in {"user", "assistant"}:
                continue

            input_items.append(
                {
                    "role": role,
                    "content": text,
                }
            )

        return input_items

    @staticmethod
    def _read_prompt(
        path: Path,
        prompt_name: str,
    ) -> str:
        if not path.exists():
            raise RuntimeError(
                f"Không tìm thấy {prompt_name}: {path}"
            )

        content = path.read_text(
            encoding="utf-8"
        ).strip()

        if not content:
            raise RuntimeError(
                f"{prompt_name} đang để trống"
            )

        return content

    @staticmethod
    def _build_tools() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": "search_warranty_policy",
                "description": (
                    "Tra cứu chính sách bảo hành, đổi hàng, "
                    "đổi size hoặc đổi mẫu chính thức."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": (
                                "Câu hỏi chính sách của khách"
                            ),
                        },
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "function",
                "name": "search_order",
                "description": (
                    "Tìm đơn hàng theo số điện thoại và "
                    "mã đơn nếu khách đã cung cấp."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "phone": {
                            "type": "string",
                            "description": (
                                "Số điện thoại đặt hàng"
                            ),
                        },
                        "order_code": {
                            "type": [
                                "string",
                                "null",
                            ],
                            "description": (
                                "Mã đơn hoặc null nếu chưa có"
                            ),
                        },
                    },
                    "required": [
                        "phone",
                        "order_code",
                    ],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "function",
                "name": "search_order_by_code",
                "description": (
                    "Tìm đơn hàng chỉ bằng mã đơn khi khách chưa "
                    "cung cấp số điện thoại."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "order_code": {
                            "type": "string",
                            "description": "Mã đơn hàng của khách",
                        },
                    },
                    "required": ["order_code"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        ]
