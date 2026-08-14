from abc import ABC, abstractmethod
from typing import Any


class AIService(ABC):
    provider_name: str
    model: str

    @abstractmethod
    def activation_conversation(
        self,
        event: str,
        context: dict[str, Any],
        customer_message: str | None = None,
    ) -> dict[str, str]:
        """Soạn phản hồi và phân loại xác nhận kích hoạt."""

    @abstractmethod
    def extract_order_from_image(
        self,
        image_bytes: bytes,
        mime_type: str,
    ) -> dict[str, Any]:
        """Trích xuất thông tin mã đơn từ ảnh."""
