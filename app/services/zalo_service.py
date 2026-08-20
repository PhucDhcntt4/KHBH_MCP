import logging
import os
from typing import Any
from urllib.parse import urlparse

import requests


logger = logging.getLogger("uvicorn.error")


class ZaloService:
    """Giao tiếp với Zalo Official Account API."""

    BASE_URL = "https://openapi.zalo.me"

    def __init__(self) -> None:
        self.access_token = os.getenv("ZALO_ACCESS_TOKEN", "").strip()
        self.webhook_secret = os.getenv("ZALO_WEBHOOK_SECRET", "").strip()
        self.timeout = int(os.getenv("ZALO_API_TIMEOUT", "10"))

    def configured(self) -> bool:
        return bool(self.access_token)

    def send_message(self, user_id: str, text: str) -> None:
        if not self.access_token:
            raise RuntimeError("Thiếu ZALO_ACCESS_TOKEN")

        user_id = str(user_id).strip()
        text = str(text).strip()
        if not user_id:
            raise ValueError("Thiếu Zalo user_id")
        if not text:
            raise ValueError("Nội dung tin nhắn không được rỗng")
        if len(text) > 2000:
            raise ValueError("Tin nhắn Zalo vượt quá 2000 ký tự")

        url = f"{self.BASE_URL}/v3.0/oa/message/cs"
        headers = {
            "access_token": self.access_token,
            "Content-Type": "application/json",
        }
        payload = {
            "recipient": {"user_id": user_id},
            "message": {"text": text},
        }
        logger.info("ZALO SEND MESSAGE user_id=%s text=%s", user_id, text)

        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            logger.exception("ZALO API REQUEST FAILED user_id=%s", user_id)
            raise RuntimeError(f"Zalo API request failed: {error}") from error

        if not response.ok:
            raise RuntimeError(
                f"Zalo API HTTP error {response.status_code}: {response.text}"
            )

        try:
            result: dict[str, Any] = response.json()
        except ValueError as error:
            raise RuntimeError("Zalo API trả về response không phải JSON") from error

        error_code = result.get("error")
        if error_code not in (None, 0):
            error_message = result.get("message", "Unknown Zalo API error")
            raise RuntimeError(f"Zalo API error {error_code}: {error_message}")

        logger.info("ZALO MESSAGE SENT SUCCESSFULLY user_id=%s", user_id)

    def send_typing(self, user_id: str) -> None:
        return None

    def download_image(self, image_id: str) -> tuple[bytes, str]:
        """Tải URL ảnh nhận từ webhook Zalo và trả bytes + tên file."""
        image_url = str(image_id).strip()
        parsed = urlparse(image_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("URL ảnh Zalo không hợp lệ")

        logger.info("ZALO DOWNLOAD IMAGE host=%s", parsed.hostname)
        try:
            response = requests.get(
                image_url,
                timeout=self.timeout,
                stream=True,
            )
            response.raise_for_status()
            logger.info(
                "ZALO IMAGE HTTP status=%s final_url=%s headers=%s",
                response.status_code,
                response.url,
                dict(response.headers),
            )
        except requests.RequestException as error:
            raise RuntimeError(f"Không thể tải ảnh Zalo: {error}") from error

        content_type = (
            response.headers.get("Content-Type", "")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        extensions = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }
        extension = extensions.get(content_type)
        if not extension:
            response.close()
            raise ValueError(
                "Định dạng ảnh Zalo không được hỗ trợ: "
                f"{content_type or 'unknown'}"
            )

        max_size = 10 * 1024 * 1024
        declared_size = response.headers.get("Content-Length")
        try:
            if declared_size and int(declared_size) > max_size:
                raise ValueError("Ảnh Zalo vượt quá giới hạn 10 MB")

            content = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                content.extend(chunk)
                if len(content) > max_size:
                    raise ValueError("Ảnh Zalo vượt quá giới hạn 10 MB")
        finally:
            response.close()

        if not content:
            raise ValueError("Ảnh Zalo không có dữ liệu")

        logger.info(
            "ZALO IMAGE DOWNLOADED bytes=%s content_type=%s",
            len(content),
            content_type,
        )
        return bytes(content), f"zalo_image{extension}"

    def verify_webhook(self, payload: bytes, signature: str | None) -> bool:
        raise NotImplementedError("Zalo webhook verification chưa được tích hợp")
