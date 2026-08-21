import re
from pathlib import Path


class PhoneValidationService:
    def __init__(self, prefix_path: str | Path) -> None:
        self.prefix_path = Path(prefix_path)
        self.valid_prefixes = self._load_prefixes()

    def _load_prefixes(self) -> set[str]:
        content = self.prefix_path.read_text(encoding="utf-8")
        return {
            line.strip()
            for line in content.splitlines()
            if line.strip()
        }

    @staticmethod
    def extract_invalid_phone(value: str | None) -> str | None:
        """Tìm chuỗi giống SĐT nhưng chứa ký tự không hợp lệ."""
        if not value:
            return None

        match = re.search(
            r"(?<![A-Za-z0-9])"
            r"(?:\+?84|0)"
            r"[A-Za-z0-9*._\-•]{5,}"
            r"(?![A-Za-z0-9])",
            value,
        )
        if not match:
            return None

        candidate = match.group(0)
        cleaned = re.sub(r"[.\-]", "", candidate)
        digits = cleaned[1:] if cleaned.startswith("+") else cleaned
        return candidate if not digits.isdigit() else None

    @staticmethod
    def normalize(value: str | None) -> str | None:
        if not value:
            return None

        # Chỉ lấy chuỗi giống SĐT bắt đầu bằng 0 hoặc +84.
        # Nhờ vậy mã đơn như SO0004127 không bị nhận nhầm.
        match = re.search(
            r"(?<![A-Za-z0-9])(?:\+?84|0)(?:[\s.\-]*\d){6,10}(?!\d)",
            value,
        )
        if not match:
            return None

        phone = re.sub(r"\D", "", match.group(0))
        if phone.startswith("84") and len(phone) == 11:
            phone = "0" + phone[2:]
        return phone or None

    def validate(self, value: str | None) -> dict:
        invalid_phone = self.extract_invalid_phone(value)
        if invalid_phone:
            return {
                "valid": False,
                "status": "invalid_phone_characters",
                "phone": invalid_phone,
            }

        phone = self.normalize(value)
        if not phone:
            return {
                "valid": False,
                "status": "phone_missing",
                "phone": None,
            }

        if len(phone) != 10:
            return {
                "valid": False,
                "status": "invalid_phone_length",
                "phone": phone,
            }

        prefix = phone[:3]
        if prefix not in self.valid_prefixes:
            return {
                "valid": False,
                "status": "invalid_phone_prefix",
                "phone": phone,
                "prefix": prefix,
            }

        return {
            "valid": True,
            "status": "valid_phone",
            "phone": phone,
            "prefix": prefix,
        }
