import re
from typing import Any

from app.models import ImageOrderInfo
from app.services.order_service import (
    normalize_order_code,
    normalize_phone,
)


class ImageExtractionService:
    """Kiểm tra đầu vào và chuẩn hóa kết quả đọc ảnh cho mọi AI provider."""

    ALLOWED_MIME_TYPES = {
        "image/jpeg",
        "image/png",
        "image/webp",
    }

    def validate_input(
        self,
        image_bytes: bytes,
        mime_type: str,
    ) -> None:
        if not image_bytes:
            raise ValueError("Ảnh không có dữ liệu")

        if mime_type not in self.ALLOWED_MIME_TYPES:
            raise ValueError(
                "Định dạng ảnh không được hỗ trợ"
            )

    def normalize(
        self,
        extracted: ImageOrderInfo,
    ) -> dict[str, Any]:
        masked_phone = re.sub(
            r"[^0-9*xX]",
            "",
            extracted.masked_phone or "",
        ) or None

        return {
            "phone": normalize_phone(extracted.phone),
            "masked_phone": masked_phone,
            "order_code": normalize_order_code(
                extracted.order_code
            ),
            "phone_confident": (
                extracted.phone_confident
            ),
            "masked_phone_confident": (
                extracted.masked_phone_confident
            ),
            "order_code_confident": (
                extracted.order_code_confident
            ),
        }
