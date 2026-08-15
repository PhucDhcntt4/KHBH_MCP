from abc import ABC, abstractmethod
from dataclasses import dataclass
import logging
from typing import Any


class ChannelLoggerAdapter(logging.LoggerAdapter):
    """Tự gắn tên channel vào mọi dòng log của adapter."""

    def process(
        self,
        message: str,
        kwargs: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        channel = self.extra.get("channel", "unknown")
        return f"channel={channel} {message}", kwargs


@dataclass
class IncomingChannelMessage:
    channel: str
    conversation_id: str
    message_id: str | None = None
    text: str | None = None
    image_id: str | None = None
    image_bytes: bytes | None = None
    image_mime_type: str | None = None


class MessageChannel(ABC):
    channel_name: str

    @abstractmethod
    def ready(self) -> bool:
        """Kiểm tra channel"""

    @abstractmethod
    def send_message(
        self,
        conversation_id: str,
        text: str,
    ) -> None:
        """Gửi tin nhắn về cho khách."""

    @abstractmethod
    def send_typing(
        self,
        conversation_id: str,
    ) -> None:
        """Gửi trạng thái đang nhập nếu channel hỗ trợ."""

    @abstractmethod
    def download_image(
        self,
        image_id: str,
    ) -> tuple[bytes, str]:
        """Tải ảnh và trả về bytes + tên file/đường dẫn."""

    @abstractmethod
    def parse_webhook(
        self,
        payload: dict,
    ) -> IncomingChannelMessage | None:
        """Chuyển payload riêng của channel về dữ liệu dùng chung."""
