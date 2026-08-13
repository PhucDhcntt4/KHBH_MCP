from abc import ABC, abstractmethod
from typing import Any


class AIService(ABC):
    provider_name: str
    model: str

    @abstractmethod
    def extract_order_from_image(
        self,
        image_bytes: bytes,
        mime_type: str,
    ) -> dict[str, Any]:
        """Trích xuất thông tin mã đơn từ ảnh."""
