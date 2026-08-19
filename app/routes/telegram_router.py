import logging
import os
import threading
from collections import deque
from typing import Any

from fastapi import (  # type: ignore
    APIRouter,
    BackgroundTasks,
    HTTPException,
    Request,
)

from app.channels.base import ChannelLoggerAdapter
from app.channels.telegram_channel import TelegramChannel
from app.services.activation_flow_service import ActivationFlowService

router = APIRouter(
    prefix="/api/telegram",
    tags=["Telegram"],
)
logger = ChannelLoggerAdapter(
    logging.getLogger("uvicorn.error"),
    {"channel": "telegram"},
)

telegram_service: TelegramChannel | None = None
activation_flow: ActivationFlowService | None = None

_update_lock = threading.Lock()
_processed_updates: deque[int] = deque(maxlen=1000)
_processed_update_set: set[int] = set()


def configure_telegram(
    channel: TelegramChannel,
    flow: ActivationFlowService,
) -> None:
    global telegram_service, activation_flow

    telegram_service = channel
    activation_flow = flow


def telegram_ready() -> bool:
    return telegram_service is not None and activation_flow is not None


def _remember_update(update_id: Any) -> bool:
    """Chống Telegram gửi lại cùng một update_id."""
    if not isinstance(update_id, int):
        return True

    with _update_lock:
        if update_id in _processed_update_set:
            return False

        if len(_processed_updates) == _processed_updates.maxlen:
            _processed_update_set.discard(_processed_updates[0])

        _processed_updates.append(update_id)
        _processed_update_set.add(update_id)
        return True


def extract_message(
    payload: dict[str, Any],
) -> tuple[int | None, str | None, str | None]:
    """Chỉ parse payload Telegram; không chứa nghiệp vụ kích hoạt."""
    message = payload.get("message")
    if not isinstance(message, dict):
        return None, None, None

    chat_id = (message.get("chat") or {}).get("id")
    if chat_id is None:
        return None, None, None

    photos = message.get("photo") or []
    file_id = photos[-1].get("file_id") if photos else None
    text = message.get("text") or message.get("caption") or ""

    return int(chat_id), str(text).strip() or None, file_id


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: dict[str, Any],
):
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    received_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token"
    )

    if secret and received_secret != secret:
        raise HTTPException(
            status_code=403,
            detail="Telegram webhook secret không hợp lệ",
        )

    if not telegram_ready():
        raise HTTPException(
            status_code=503,
            detail="Telegram service chưa sẵn sàng",
        )

    if not _remember_update(payload.get("update_id")):
        return {"status": "duplicate"}

    assert telegram_service is not None
    assert activation_flow is not None

    chat_id, text, file_id = extract_message(payload)

    if chat_id is not None and file_id:
        background_tasks.add_task(
            activation_flow.process_image,
            telegram_service,
            chat_id,
            file_id,
            text,
        )
    elif chat_id is not None and text:
        background_tasks.add_task(
            activation_flow.process_message,
            telegram_service,
            chat_id,
            text,
        )

    return {"status": "received"}
