from contextlib import asynccontextmanager
import logging

from dotenv import load_dotenv # type: ignore
from fastapi import FastAPI  # type: ignore
from app.services.AI.base import AIService
from app.services.AI.factory import create_ai_service


load_dotenv()

from app.routes.telegram_router import (  # noqa: E402
    configure_telegram,
    router as telegram_router,
    telegram_ready,
)

logger = logging.getLogger(__name__)


ai_service: AIService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ai_service

    try:
        ai_service = create_ai_service()
        logger.info(
            "AI service is ready provider=%s model=%s",
            ai_service.provider_name,
            ai_service.model,
        )

        try:
            configure_telegram(ai_service)
            logger.info("Telegram Bot is ready")
        except Exception as error:
            logger.exception(
                "Telegram Bot initialization failed: %s",
                error,
            )

    except Exception as error:
        ai_service = None
        logger.exception(
            "AI service initialization failed: %s",
            error,
        )

    yield


app = FastAPI(
    title="Warranty Agent",
    version="0.2.0",
    lifespan=lifespan,
)
app.include_router(telegram_router)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "ai_ready": ai_service is not None,
        "ai_provider": (
            ai_service.provider_name
            if ai_service is not None
            else None
        ),
        "ai_model": (
            ai_service.model
            if ai_service is not None
            else None
        ),
        "telegram_ready": telegram_ready(),
    }
