import logging
import os

from app.channels.base import IncomingChannelMessage, MessageChannel
from app.services.zalo_service import ZaloService


logger = logging.getLogger("uvicorn.error")


class ZaloChannel(MessageChannel):
    channel_name = "zalo"

    def __init__(self, service: ZaloService | None = None) -> None:
        self.service = service or ZaloService()
        self.test_mode = os.getenv("ZALO_TEST_MODE", "false").lower() == "true"
        self.allowed_users = {
            value.strip()
            for value in os.getenv("ZALO_ALLOWED_USERS", "").split(",")
            if value.strip()
        }

    def ready(self) -> bool:
        return self.service.configured()

    def send_message(self, conversation_id: str, text: str) -> None:
        self.service.send_message(user_id=conversation_id, text=text)

    def send_typing(self, conversation_id: str) -> None:
        self.service.send_typing(user_id=conversation_id)

    def download_image(self, image_id: str) -> tuple[bytes, str]:
        return self.service.download_image(image_id)

    @staticmethod
    def _image_url_from_message(message: dict) -> str | None:
        attachments = message.get("attachments") or []
        if isinstance(attachments, dict):
            attachments = [attachments]

        if isinstance(attachments, list):
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                if attachment.get("type") not in (None, "image"):
                    continue

                image_payload = attachment.get("payload") or {}
                if not isinstance(image_payload, dict):
                    continue

                image_url = str(
                    image_payload.get("url")
                    or image_payload.get("original_url")
                    or image_payload.get("thumbnail")
                    or ""
                ).strip()
                if image_url:
                    return image_url

        attachment = message.get("attachment") or {}
        if isinstance(attachment, dict):
            image_payload = attachment.get("payload") or attachment
            if isinstance(image_payload, dict):
                image_url = str(
                    image_payload.get("url")
                    or image_payload.get("original_url")
                    or image_payload.get("thumbnail")
                    or ""
                ).strip()
                if image_url:
                    return image_url

        return str(message.get("url") or "").strip() or None

    def parse_webhook(self, payload: dict) -> IncomingChannelMessage | None:
        event_name = str(payload.get("event_name") or "").strip()
        if event_name not in {"user_send_text", "user_send_image"}:
            logger.info("ZALO EVENT IGNORED event=%s", event_name)
            return None

        sender = payload.get("sender") or {}
        sender_id = str(sender.get("id") or "").strip()
        if not sender_id:
            logger.warning("ZALO EVENT IGNORED reason=missing_sender")
            return None

        if (
            self.test_mode
            and self.allowed_users
            and sender_id not in self.allowed_users
        ):
            logger.info("ZALO SENDER IGNORED sender=%s", sender_id)
            return None

        message = payload.get("message") or {}
        if not isinstance(message, dict):
            return None

        message_id = str(message.get("msg_id") or "").strip() or None
        text = str(
            message.get("text") or message.get("caption") or ""
        ).strip() or None
        image_url = (
            self._image_url_from_message(message)
            if event_name == "user_send_image"
            else None
        )

        if event_name == "user_send_image" and not image_url:
            logger.warning(
                "ZALO IMAGE IGNORED sender=%s reason=missing_image_url "
                "message_keys=%s",
                sender_id,
                sorted(message.keys()),
            )
            return None

        if not text and not image_url:
            return None

        logger.info(
            "ZALO MESSAGE sender=%s message_id=%s event=%s "
            "has_text=%s has_image=%s",
            sender_id,
            message_id,
            event_name,
            bool(text),
            bool(image_url),
        )
        return IncomingChannelMessage(
            channel=self.channel_name,
            conversation_id=sender_id,
            message_id=message_id,
            text=text,
            image_id=image_url,
        )
