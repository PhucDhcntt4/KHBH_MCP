import re
from pathlib import Path


class PhoneValidationService:
    def __init__(
        self,
        prefix_path: str | Path,
    ) -> None:
        self.prefix_path = Path(prefix_path)
        self.valid_prefixes = self._load_prefixes()

    def _load_prefixes(self) -> set[str]:
        content = self.prefix_path.read_text(
            encoding="utf-8"
        )

        return {
            line.strip()
            for line in content.splitlines()
            if line.strip()
        }

    @staticmethod
    def normalize(value: str | None) -> str | None:
        if not value:
            return None

        phone = re.sub(r"\D", "", value)

        if phone.startswith("84") and len(phone) == 11:
            phone = "0" + phone[2:]

        return phone or None

    def validate(
        self,
        value: str | None,
    ) -> dict:
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