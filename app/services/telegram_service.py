import os
import re
from typing import Any

import requests


class TelegramService:
    def __init__(self) -> None:
        token = os.getenv("TELEGRAM_BOT_TOKEN")

        if not token:
            raise RuntimeError("Thiếu TELEGRAM_BOT_TOKEN trong file .env")

        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN không đúng định dạng "
                "(phải gồm bot_id:dãy_ký_tự_bí_mật)"
            )

        self.base_url = (
            f"https://api.telegram.org/bot{token}"
        )

        self.file_base_url = (
            f"https://api.telegram.org/file/bot{token}"
        )

    def send_message(
        self,
        chat_id: int | str,
        text: str,
    ) -> dict[str, Any]:
        text = self.format_message(text)

        response = requests.post(
            f"{self.base_url}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
            },
            timeout=30,
        )

        response.raise_for_status()
        result = response.json()

        if not result.get("ok"):
            raise RuntimeError(
                f"Telegram sendMessage failed: {result}"
            )
        return result

    @staticmethod
    def format_message(text: str) -> str:
        """Chuyển Markdown đơn giản thành văn bản dễ đọc trên Telegram."""
        formatted = text.replace("\r\n", "\n").strip()

        # Telegram đang gửi plain text nên bỏ ký hiệu Markdown in đậm.
        formatted = re.sub(
            r"\*\*(.+?)\*\*",
            r"\1",
            formatted,
        )

        lines: list[str] = []

        for raw_line in formatted.split("\n"):
            line = raw_line.rstrip()
            stripped = line.lstrip()
            indent = line[: len(line) - len(stripped)]

            if re.match(r"^[-*]\s+", stripped):
                content = re.sub(
                    r"^[-*]\s+",
                    "",
                    stripped,
                    count=1,
                )
                line = f"{indent}• {content}"
            elif re.match(r"^#{1,6}\s+", stripped):
                line = re.sub(
                    r"^#{1,6}\s+",
                    "",
                    stripped,
                    count=1,
                )

            lines.append(line)

        formatted = "\n".join(lines)
        formatted = re.sub(r"\n{3,}", "\n\n", formatted)

        return formatted.strip()

    def send_typing(
        self,
        chat_id: int | str,
    ) -> dict[str, Any]:

        response = requests.post(
            f"{self.base_url}/sendChatAction",
            json={
                "chat_id": chat_id,
                "action": "typing",
            },
            timeout=15,
        )

        response.raise_for_status()
        result = response.json()

        if not result.get("ok"):
            raise RuntimeError(
                f"Telegram sendChatAction failed: {result}"
            )

        return result

    def download_file(
        self,
        file_id: str,
    ) -> tuple[bytes, str]:

        response = requests.get(
            f"{self.base_url}/getFile",
            params={"file_id": file_id},
            timeout=20,
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            raise RuntimeError("Không lấy được hình ảnh")

        file_path = result["result"]["file_path"]

        file_response = requests.get(
            f"{self.file_base_url}/{file_path}",
            timeout=30,
        )

        file_response.raise_for_status()

        if len(file_response.content) > 20 * 1024 * 1024:
            raise RuntimeError("Ảnh vượt quá giới hạn 20 MB")

        return file_response.content, file_path

    def send_photo(
        self,
        chat_id: int | str,
        photo_url: str,
        caption: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "photo": photo_url,
        }

        if caption:
            payload["caption"] = caption[:1024]

        response = requests.post(
            f"{self.base_url}/sendPhoto",
            json=payload,
            timeout=30,
        )

        response.raise_for_status()
        result = response.json()

        if not result.get("ok"):
            raise RuntimeError(
                f"Telegram sendPhoto failed: {result}"
            )

        return result

    def send_media_group(
        self,
        chat_id: int | str,
        photo_urls: list[str],
    ) -> list[dict[str, Any]]:
        unique_urls = list (
            dict.fromkeys(
                url for url in photo_urls if url
            )
        )

        if len(unique_urls) < 2:
            if unique_urls:
                result = self.send_photo(
                    chat_id=chat_id,
                    photo_url=unique_urls[0],
                )
                return [result]
            return []

        media = [
            {
                "type": "photo",
                "media": photo_url,
            }

            for photo_url in unique_urls[:10]
        ]

        response = requests.post(
            f"{self.base_url}/sendMediaGroup",
            json={
                "chat_id": chat_id,
                "media": media,
            },
            timeout=60,
        )

        try:
            result = response.json()
        except ValueError as exc:
            raise RuntimeError(
                "Telegram sendMediaGroup returned invalid JSON"
            ) from exc

        if not response.ok or not result.get("ok"):
            raise RuntimeError(
                "Telegram sendMediaGroup failed: "
                f"{result.get('description', 'unknown error')}"
            )

        return result.get("result", [])
