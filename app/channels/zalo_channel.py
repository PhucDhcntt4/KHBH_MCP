from app.channels.base import (
    IncomingChannelMessage,
    MessageChannel,
)
from app.services.zalo_service import ZaloService


class ZaloChannel(MessageChannel):
    channel_name = "zalo"

    def __init__(
        self,
        service: ZaloService | None = None,
    ) -> None:
        self.service = service or ZaloService()

    def ready(self) -> bool:
        # Dù đã có token, phần API vẫn chưa triển khai.
        # Chỉ đổi thành True sau khi hoàn thiện integration.
        return False

    def send_message(
        self,
        conversation_id: str,
        text: str,
    ) -> None:
        self.service.send_message(
            user_id=conversation_id,
            text=text,
        )

    def send_typing(
        self,
        conversation_id: str,
    ) -> None:
        self.service.send_typing(
            user_id=conversation_id,
        )

    def download_image(
        self,
        image_id: str,
    ) -> tuple[bytes, str]:
        return self.service.download_image(image_id)

    def parse_webhook(
        self,
        payload: dict,
    ) -> IncomingChannelMessage | None:
        """
        Sau này đọc payload webhook Zalo tại đây.

        Cần lấy:
        - user_id
        - message_id
        - text
        - image URL hoặc image ID

        Hiện chưa biết payload chính thức nên chưa tự đoán.
        """
        return None