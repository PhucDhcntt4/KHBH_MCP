from abc import ABC, abstractmethod
from typing import Any


class AIService(ABC):
    provider_name: str
    model: str

    @abstractmethod
    def chat(
        self,
        message: str,
        customer_id: str,
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Trả lời tin nhắn và có thể gọi các tool nghiệp vụ."""

    @abstractmethod
    def compose_reply(
        self,
        event: str,
        history: list[dict[str, Any]] | None = None,
    ) -> str:
        """Soạn câu trả lời từ kết quả nghiệp vụ đã được xác minh."""

    @abstractmethod
    def classify_confirmation_intent(
        self,
        message: str,
    ) -> str:
        """Trả về confirm, cancel hoặc unknown."""

    @abstractmethod
    def extract_order_from_image(
        self,
        image_bytes: bytes,
        mime_type: str,
    ) -> dict[str, Any]:
        """Trích xuất số điện thoại và mã đơn từ ảnh."""
