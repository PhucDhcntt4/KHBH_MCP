import os

from app.services.AI.base import AIService
from app.services.AI.providers.gemini_provider import (
    GeminiProvider,
)


def create_ai_service() -> AIService:
    provider = os.getenv(
        "AI_PROVIDER",
        "gemini",
    ).strip().lower()

    if provider == "gemini":
        return GeminiProvider()

    if provider == "openai":
        from app.services.AI.providers.openai_provider import (
            OpenAIProvider,
        )

        return OpenAIProvider()

    if provider == "claude":
        raise RuntimeError(
            "ClaudeProvider chưa được triển khai"
        )

    raise RuntimeError(
        f"AI_PROVIDER không được hỗ trợ: {provider}"
    )