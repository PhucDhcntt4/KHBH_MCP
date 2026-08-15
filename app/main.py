from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI  # type: ignore

from app.channels.factory import create_channels
from app.channels.telegram_channel import TelegramChannel
from app.channels.zalo_channel import ZaloChannel
from app.config import BOT_CHANNELS
from app.routes.telegram_router import (
    configure_telegram,
    router as telegram_router,
    telegram_ready,
)
from app.routes.zalo_router import (
    configure_zalo,
    router as zalo_router,
    zalo_ready,
)
from app.services.AI.base import AIService
from app.services.AI.factory import create_ai_service


logger = logging.getLogger(__name__)
ai_service: AIService | None = None
channels = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ai_service, channels

    try:
        ai_service = create_ai_service()
        logger.info(
            "AI service is ready provider=%s model=%s",
            ai_service.provider_name,
            ai_service.model,
        )
        channels = create_channels(BOT_CHANNELS)

        telegram = channels.get("telegram")
        if isinstance(telegram, TelegramChannel):
            configure_telegram(ai_service, telegram)
            logger.info("Telegram channel is ready")

        zalo = channels.get("zalo")
        if isinstance(zalo, ZaloChannel):
            configure_zalo(zalo)
            logger.warning("Zalo channel is enabled but not implemented")
    except Exception as error:
        ai_service = None
        channels = {}
        logger.exception("Application initialization failed: %s", error)

    yield


app = FastAPI(
    title="Warranty Agent",
    version="0.3.0",
    lifespan=lifespan,
)

if "telegram" in BOT_CHANNELS:
    app.include_router(telegram_router)

if "zalo" in BOT_CHANNELS:
    app.include_router(zalo_router)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "ai_ready": ai_service is not None,
        "ai_provider": (
            ai_service.provider_name if ai_service is not None else None
        ),
        "ai_model": ai_service.model if ai_service is not None else None,
        "enabled_channels": sorted(BOT_CHANNELS),
        "telegram_ready": (
            telegram_ready() if "telegram" in BOT_CHANNELS else False
        ),
        "zalo_ready": zalo_ready() if "zalo" in BOT_CHANNELS else False,
    }
