import os
from pathlib import Path

from dotenv import load_dotenv  # type: ignore


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
PROMPT_DIR = PROJECT_ROOT / "prompts"

IMAGE_ORDER_EXTRACTION_PROMPT_PATH = (
    PROMPT_DIR / "image_order_extraction.txt"
)
PHONE_PREFIX_PATH = PROJECT_ROOT / "Dau_so_check.txt"
ACTIVATION_DATA_PATH = PROJECT_ROOT / "data" / "activation_requests.json"
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
