import threading
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.config import ACTIVATION_LOG_PATH
from app.services.MCP_Business_client import MCPBusinessClient


class ActivationService:
    """Kích hoạt qua MCP và append nhật ký dạng text."""

    _VIETNAM_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")

    def __init__(
        self,
        client: MCPBusinessClient | None = None,
        log_path: Path | None = None,
    ) -> None:
        self.client = client or MCPBusinessClient()
        self.log_path = log_path or ACTIVATION_LOG_PATH
        self._lock = threading.Lock()

    @staticmethod
    def _now() -> str:
        return datetime.now(ActivationService._VIETNAM_TIMEZONE).isoformat()

    @staticmethod
    def _normalize_message(value: Any) -> str:
        normalized = unicodedata.normalize("NFD", str(value or "").casefold())
        text = "".join(
            character
            for character in normalized
            if unicodedata.category(character) != "Mn"
        ).strip()
        return text.replace("đ", "d")

    @staticmethod
    def _safe(value: Any) -> str:
        return str(value if value is not None else "-").replace("\r", " ").replace(
            "\n", " "
        ).replace("|", "/")

    def _append_log(
        self,
        *,
        request_id: str,
        chat_id: str | int,
        phone: str,
        order_number: str,
        sales_channel: str | None,
        source_channel: str,
        status: str,
        mcp_status: Any = None,
        mcp_message: Any = None,
        error: Any = None,
    ) -> None:
        line = " | ".join(
            [
                f"time={self._now()}",
                "event=activation",
                f"status={self._safe(status)}",
                f"request_id={self._safe(request_id)}",
                f"chat_id={self._safe(chat_id)}",
                f"source_channel={self._safe(source_channel)}",
                f"order_number={self._safe(order_number)}",
                f"sales_channel={self._safe(sales_channel)}",
                f"phone={self._safe(phone)}",
                f"mcp_status={self._safe(mcp_status)}",
                f"mcp_message={self._safe(mcp_message)}",
                f"error={self._safe(error)}",
            ]
        )
        with self._lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(line + "\n")

    def activate(
        self,
        *,
        chat_id: str | int,
        phone: str,
        order_number: str,
        channel: str | None = None,
        source_channel: str = "telegram",
    ) -> dict[str, Any]:
        request_id = uuid4().hex
        record: dict[str, Any] = {
            "request_id": request_id,
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
        self._append_log(
            request_id=request_id,
            chat_id=chat_id,
            phone=phone,
            order_number=order_number,
            sales_channel=channel,
            source_channel=source_channel,
            status="processing",
        )

        try:
            result = self.client.call_tool(
                "activate_order",
                {"phone": phone, "order_number": order_number},
            )
            success = isinstance(result, dict) and result.get("status") is True
            message = self._normalize_message(
                result.get("message") if isinstance(result, dict) else None
            )
            already_activated = (
                message == "khong xu ly"
                or "xu ly truoc do" in message
                or "kich hoat truoc do" in message
            )

            if already_activated:
                record["status"] = "already_activated"
            elif success:
                record["status"] = "activated"
            else:
                record["status"] = "failed"

            record["mcp_result"] = result
            record["updated_at"] = self._now()
            self._append_log(
                request_id=request_id,
                chat_id=chat_id,
                phone=phone,
                order_number=order_number,
                sales_channel=channel,
                source_channel=source_channel,
                status=str(record["status"]),
                mcp_status=(result.get("status") if isinstance(result, dict) else None),
                mcp_message=(result.get("message") if isinstance(result, dict) else None),
            )
            return record
        except Exception as error:
            record["status"] = "failed"
            record["error"] = str(error)
            record["updated_at"] = self._now()
            self._append_log(
                request_id=request_id,
                chat_id=chat_id,
                phone=phone,
                order_number=order_number,
                sales_channel=channel,
                source_channel=source_channel,
                status="failed",
                error=error,
            )
            raise
