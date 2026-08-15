from app.channels.base import IncomingChannelMessage, MessageChannel
from app.services.telegram_service import TelegramService


class TelegramChannel(MessageChannel):
    channel_name = "telegram"

    def __init__(
        self,
        service: TelegramService | None = None,
    ) -> None:
        self.service = service or TelegramService()

    def ready(self) -> bool:
        return True

    def send_message(
        self,
        conversation_id: str | int,
        text: str,
    ) -> None:
        self.service.send_message(conversation_id, text)

    def send_typing(self, conversation_id: str | int) -> None:
        self.service.send_typing(conversation_id)

    def download_image(self, image_id: str) -> tuple[bytes, str]:
        return self.service.download_file(image_id)

    def parse_webhook(
        self,
        payload: dict,
    ) -> IncomingChannelMessage | None:
        message = payload.get("message")
        if not isinstance(message, dict):
            return None

        chat_id = (message.get("chat") or {}).get("id")
        if chat_id is None:
            return None

        photos = message.get("photo") or []
        image_id = photos[-1].get("file_id") if photos else None
        text = message.get("text") or message.get("caption") or ""
        message_id = message.get("message_id")

        return IncomingChannelMessage(
            channel=self.channel_name,
            conversation_id=str(chat_id),
            message_id=(str(message_id) if message_id is not None else None),
            text=str(text).strip() or None,
            image_id=image_id,
        )
