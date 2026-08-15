import os


class ZaloService:
    """
    Lớp giao tiếp với Zalo OA API.

    Hiện chỉ giữ cấu hình.
    Chưa triển khai gửi tin, tải ảnh hoặc xác minh webhook.
    """

    def __init__(self) -> None:
        self.access_token = os.getenv(
            "ZALO_ACCESS_TOKEN",
            "",
        ).strip()

        self.oa_id = os.getenv(
            "ZALO_OA_ID",
            "",
        ).strip()

        self.webhook_secret = os.getenv(
            "ZALO_WEBHOOK_SECRET",
            "",
        ).strip()

    def configured(self) -> bool:
        return bool(
            self.access_token
            and self.oa_id
            and self.webhook_secret
        )

    def send_message(
        self,
        user_id: str,
        text: str,
    ) -> None:
        raise NotImplementedError(
            "Zalo send_message chưa được tích hợp"
        )

    def send_typing(
        self,
        user_id: str,
    ) -> None:
        # Sau này kiểm tra Zalo OA API có hỗ trợ
        # trạng thái typing hay không.
        return None

    def download_image(
        self,
        image_id: str,
    ) -> tuple[bytes, str]:
        raise NotImplementedError(
            "Zalo download_image chưa được tích hợp"
        )

    def verify_webhook(
        self,
        payload: bytes,
        signature: str | None,
    ) -> bool:
        raise NotImplementedError(
            "Zalo webhook verification chưa được tích hợp"
        )