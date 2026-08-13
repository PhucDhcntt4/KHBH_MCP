import json
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import ACTIVATION_DATA_PATH
from app.services.MCP_Business_client import MCPBusinessClient


class ActivationService:
    """Ghi log JSON và kích hoạt bảo hành qua MCP."""

    def __init__(
        self,
        client: MCPBusinessClient | None = None,
        data_path: Path | None = None,
    ) -> None:
        self.client = client or MCPBusinessClient()
        self.data_path = data_path or ACTIVATION_DATA_PATH
        self._lock = threading.Lock()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_message(value: Any) -> str:
        normalized = unicodedata.normalize("NFD", str(value or "").casefold())
        return "".join(
            character
            for character in normalized
            if unicodedata.category(character) != "Mn"
        ).strip()

    def _read_records(self) -> list[dict[str, Any]]:
        if not self.data_path.exists():
            return []

        try:
            data = json.loads(self.data_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

        return data if isinstance(data, list) else []

    def _write_records(self, records: list[dict[str, Any]]) -> None:
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.data_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temporary_path.replace(self.data_path)

    def _save_record(self, record: dict[str, Any]) -> None:
        with self._lock:
            records = self._read_records()
            for index, current in enumerate(records):
                if current.get("request_id") == record["request_id"]:
                    records[index] = record
                    break
            else:
                records.append(record)
            self._write_records(records)

    def activate(
        self,
        *,
        chat_id: int,
        phone: str,
        order_number: str,
        channel: str | None = None,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "request_id": uuid4().hex,
            "chat_id": chat_id,
            "phone": phone,
            "order_number": order_number,
            "channel": channel,
            "status": "processing",
            "created_at": self._now(),
            "updated_at": self._now(),
            "mcp_result": None,
            "error": None,
        }
        self._save_record(record)

        try:
            result = self.client.call_tool(
                "activate_order",
                {
                    "phone": phone,
                    "order_number": order_number,
                },
            )
            success = isinstance(result, dict) and result.get("status") is True
            message = self._normalize_message(
                result.get("message") if isinstance(result, dict) else None
            )
            if success and message == "khong xu ly":
                record["status"] = "already_activated"
            elif success:
                record["status"] = "activated"
            else:
                record["status"] = "failed"
            record["mcp_result"] = result
            record["updated_at"] = self._now()
            self._save_record(record)
            return record
        except Exception as error:
            record["status"] = "failed"
            record["error"] = str(error)
            record["updated_at"] = self._now()
            self._save_record(record)
            raise
