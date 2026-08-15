import logging
from typing import Any

from fastapi import APIRouter, HTTPException  # type: ignore

from app.channels.base import ChannelLoggerAdapter
from app.channels.zalo_channel import ZaloChannel


router = APIRouter(prefix="/api/zalo", tags=["Zalo"])
logger = ChannelLoggerAdapter(
    logging.getLogger("uvicorn.error"),
    {"channel": "zalo"},
)
zalo_channel: ZaloChannel | None = None


def configure_zalo(channel: ZaloChannel) -> None:
    global zalo_channel
    zalo_channel = channel


def zalo_ready() -> bool:
    return zalo_channel is not None and zalo_channel.ready()


@router.get("/health")
def zalo_health() -> dict[str, Any]:
    return {
        "status": "not_implemented",
        "configured": bool(
            zalo_channel and zalo_channel.service.configured()
        ),
        "ready": zalo_ready(),
    }


@router.post("/webhook")
async def zalo_webhook(payload: dict[str, Any]):
    logger.warning("ZALO WEBHOOK IGNORED reason=not_implemented")
    raise HTTPException(
        status_code=503,
        detail="Zalo channel chưa được tích hợp",
    )
