import logging
import threading
from collections import deque
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException  # type: ignore

from app.channels.base import ChannelLoggerAdapter
from app.channels.zalo_channel import ZaloChannel
from app.services.activation_flow_service import ActivationFlowService


# Router

router = APIRouter(
    prefix="/api/zalo",
    tags=["Zalo"],
)


# Logger

logger = ChannelLoggerAdapter(
    logging.getLogger("uvicorn.error"),
    {"channel": "zalo"},
)

# Zalo Channel

zalo_channel: ZaloChannel | None = None
activation_flow: ActivationFlowService | None = None
_message_lock = threading.Lock()
_processed_messages: deque[str] = deque(maxlen=1000)
_processed_message_set: set[str] = set()


def configure_zalo(
    channel: ZaloChannel,
    flow: ActivationFlowService,
) -> None:
    """
    Gắn ZaloChannel đã được khởi tạo từ main.py
    vào router.

    Hàm này được gọi trong lifespan của FastAPI.
    """

    global zalo_channel, activation_flow
    zalo_channel = channel
    activation_flow = flow

    logger.info("Zalo channel configured")


def zalo_ready() -> bool:
    """
    Kiểm tra Zalo channel đã được khởi tạo
    và ZaloService đã có cấu hình hợp lệ hay chưa.
    """

    return (
        zalo_channel is not None
        and activation_flow is not None
        and zalo_channel.ready()
    )


def _remember_message(message_id: str | None) -> bool:
    """Chống Zalo gửi lại cùng một message_id."""
    if not message_id:
        return True

    with _message_lock:
        if message_id in _processed_message_set:
            return False

        if len(_processed_messages) == _processed_messages.maxlen:
            _processed_message_set.discard(_processed_messages[0])

        _processed_messages.append(message_id)
        _processed_message_set.add(message_id)
        return True


# Health Check

@router.get("/health")
def zalo_health() -> dict[str, Any]:
    """
    Kiểm tra trạng thái Zalo integration.

    URL:
        GET /api/zalo/health
    """

    configured = bool(
        zalo_channel
        and zalo_channel.service.configured()
    )

    ready = zalo_ready()

    return {
        "status": "ok" if ready else "not_ready",
        "configured": configured,
        "ready": ready,
    }

# Webhook

@router.post("/webhook")
async def zalo_webhook(
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
) -> dict[str, str]:

    if zalo_channel is None or activation_flow is None:
        logger.error(
            "Webhook received but Zalo channel is not ready"
        )

        raise HTTPException(
            status_code=503,
            detail="Zalo channel not ready",
        )

    event_name = payload.get("event_name")

    logger.info(
        "Webhook received event=%s",
        event_name,
    )

    msg = zalo_channel.parse_webhook(payload)

    if msg is None:
        logger.info(
            "Webhook ignored event=%s",
            event_name,
        )

        return {
            "status": "ignored",
        }

    if not _remember_message(msg.message_id):
        logger.info(
            "Webhook duplicate message_id=%s",
            msg.message_id,
        )
        return {
            "status": "duplicate",
        }

    logger.info(
        "ZALO WEBHOOK MESSAGE "
        "sender=%s message_id=%s text=%s",
        msg.conversation_id,
        msg.message_id,
        msg.text,
    )

    if msg.image_id:
        background_tasks.add_task(
            activation_flow.process_image,
            zalo_channel,
            msg.conversation_id,
            msg.image_id,
            msg.text,
        )
    elif msg.text:
        background_tasks.add_task(
            activation_flow.process_message,
            zalo_channel,
            msg.conversation_id,
            msg.text,
        )

    logger.info(
        "ZALO WEBHOOK PROCESSED sender=%s",
        msg.conversation_id,
    )

    return {
        "status": "received",
    }
