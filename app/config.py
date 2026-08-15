import os
from pathlib import Path

from dotenv import load_dotenv  # type: ignore


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
PROMPT_DIR = PROJECT_ROOT / "prompts"

IMAGE_ORDER_EXTRACTION_PROMPT_PATH = (
    PROMPT_DIR / "image_order_extraction.txt"
)
ACTIVATION_CONVERSATION_PROMPT_PATH = (
    PROMPT_DIR / "activation_conversation.txt"
)
PHONE_PREFIX_PATH = PROJECT_ROOT / "Dau_so_check.txt"
ACTIVATION_LOG_PATH = PROJECT_ROOT / "logs" / "activation.log"
BOT_CHANNELS = {
    channel.strip().lower()
    for channel in os.getenv("BOT_CHANNELS", "telegram").split(",")
    if channel.strip()
}
MCP_URL = os.getenv(
    "MCP_URL",
    "",
).strip()

MCP_TIMEOUT = float(
    os.getenv(
        "MCP_TIMEOUT",
        "20",
    )
)
