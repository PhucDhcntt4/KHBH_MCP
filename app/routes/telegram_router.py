import logging
import os
import re
import threading
import time
import unicodedata
from collections import deque
from typing import Any

from fastapi import (  # type: ignore
    APIRouter,
    BackgroundTasks,
    HTTPException,
    Request,
)

from app.services.AI.base import AIService
from app.services.activation_service import ActivationService
from app.config import PHONE_PREFIX_PATH
from app.services.order_service import OrderService
from app.services.phone_validation_service import PhoneValidationService
from app.services.telegram_service import TelegramService


router = APIRouter(
    prefix="/api/telegram",
    tags=["Telegram"],
)
logger = logging.getLogger("uvicorn.error")

ai_service: AIService | None = None
telegram_service: TelegramService | None = None
order_service = OrderService()
activation_service = ActivationService()
phone_validator = PhoneValidationService(PHONE_PREFIX_PATH)

_state_lock = threading.Lock()
_processed_updates: deque[int] = deque(maxlen=1000)
_processed_update_set: set[int] = set()
_pending_activation_requests: dict[int, dict[str, Any]] = {}
_ready_activations: dict[int, dict[str, Any]] = {}
_PENDING_TTL_SECONDS = 10 * 60


def configure_telegram(warranty_agent: AIService) -> None:
    global ai_service, telegram_service
    ai_service = warranty_agent
    telegram_service = TelegramService()


def telegram_ready() -> bool:
    return ai_service is not None and telegram_service is not None


def _mask_phone(phone: str) -> str:
    if len(phone) < 7:
        return "***"
    return f"{phone[:3]}****{phone[-3:]}"


def _extract_order_code(text: str) -> str | None:
    match = re.search(r"\b[A-Za-z]{1,5}\d{5,}\b", text)
    return match.group(0).upper() if match else None


def _is_activation_request(text: str) -> bool:
    normalized = unicodedata.normalize("NFD", text.casefold())
    normalized = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return "kich hoat" in normalized and "bao hanh" in normalized


def _save_pending_activation_request(chat_id: int, phone: str) -> None:
    with _state_lock:
        _pending_activation_requests[chat_id] = {
            "phone": phone,
            "created_at": time.monotonic(),
        }


def _peek_pending_activation_request(
    chat_id: int,
) -> dict[str, Any] | None:
    with _state_lock:
        pending = _pending_activation_requests.get(chat_id)
        if not pending:
            return None

        if time.monotonic() - pending["created_at"] > _PENDING_TTL_SECONDS:
            _pending_activation_requests.pop(chat_id, None)
            return None

        return dict(pending)


def _clear_pending_activation_request(chat_id: int) -> None:
    with _state_lock:
        _pending_activation_requests.pop(chat_id, None)


def _prepare_activation(
    chat_id: int,
    phone: str,
    order: dict[str, Any],
) -> None:
    with _state_lock:
        _ready_activations[chat_id] = {
            "phone": phone,
            "order_code": order["order_code"],
            "external_order_id": order.get("external_order_id"),
            "channel": order.get("channel"),
            "status": "ready_for_activation",
            "created_at": time.time(),
        }


def _remember_update(update_id: Any) -> bool:
    if not isinstance(update_id, int):
        return True

    with _state_lock:
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


def _verify_order_for_activation(
    chat_id: int,
    phone: str,
    order_code: str,
) -> str:
    try:
        order = order_service.get_by_order_code(
            order_code,
            lookup_phone=phone,
        )
    except Exception:
        logger.exception(
            "PRE ACTIVATION ORDER LOOKUP ERROR order_code=%s",
            order_code,
        )
        return (
            "Dạ hệ thống xác minh đơn hàng đang gián đoạn. "
            "Anh/chị vui lòng thử lại sau giúp em ạ."
        )

    if not order:
        return (
            f"Dạ em chưa tìm thấy mã đơn {order_code}. Anh/chị vui "
            "lòng kiểm tra và gửi lại mã đơn hoặc ảnh đơn hàng ạ."
        )

    try:
        activation = activation_service.activate(
            chat_id=chat_id,
            phone=phone,
            order_number=order["order_code"],
            channel=order.get("channel"),
        )
    except Exception:
        logger.exception(
            "ACTIVATION ERROR chat_id=%s order_code=%s channel=%s phone=%s",
            chat_id,
            order["order_code"],
            order.get("channel") or "unknown",
            _mask_phone(phone),
        )
        return (
            "Dạ em đã xác minh được đơn hàng nhưng hệ thống "
            "kích hoạt đang gián đoạn. Anh/chị vui lòng thử lại sau ạ."
        )

    if activation["status"] == "already_activated":
        _prepare_activation(chat_id, phone, order)
        with _state_lock:
            _ready_activations[chat_id]["status"] = "already_activated"
            _ready_activations[chat_id]["request_id"] = activation["request_id"]
        _clear_pending_activation_request(chat_id)
        logger.info(
            "ACTIVATION ALREADY EXISTS chat_id=%s order_code=%s "
            "channel=%s phone=%s",
            chat_id,
            order["order_code"],
            order.get("channel") or "unknown",
            _mask_phone(phone),
        )
        return (
            f"Dạ, mã đơn {order['order_code']} với số điện thoại "
            f"{phone} đã được kích hoạt bảo hành trước đó rồi ạ."
        )

    if activation["status"] != "activated":
        logger.warning(
            "ACTIVATION REJECTED chat_id=%s order_code=%s channel=%s phone=%s",
            chat_id,
            order["order_code"],
            order.get("channel") or "unknown",
            _mask_phone(phone),
        )
        return (
            "Dạ em đã xác minh được đơn hàng nhưng chưa thể "
            "kích hoạt bảo hành. Anh/chị vui lòng thử lại sau ạ."
        )

    _prepare_activation(chat_id, phone, order)
    with _state_lock:
        _ready_activations[chat_id]["status"] = "activated"
        _ready_activations[chat_id]["request_id"] = activation["request_id"]
    _clear_pending_activation_request(chat_id)
    logger.info(
        "ACTIVATION SUCCESS chat_id=%s order_code=%s "
        "channel=%s phone=%s",
        chat_id,
        order["order_code"],
        order.get("channel") or "unknown",
        _mask_phone(phone),
    )
    return (
        f"Dạ em đã kích hoạt bảo hành thành công cho mã đơn "
        f"{order['order_code']} với số điện thoại {phone} ạ."
    )


def _handle_activation_message(chat_id: int, text: str) -> bool:
    assert telegram_service is not None

    pending = _peek_pending_activation_request(chat_id)
    order_code = _extract_order_code(text)

    if pending and order_code:
        phone = str(pending["phone"])
        telegram_service.send_typing(chat_id)
        reply = _verify_order_for_activation(
            chat_id,
            phone,
            order_code,
        )
        telegram_service.send_message(chat_id, reply)
        return True

    if not _is_activation_request(text):
        return False

    validation = phone_validator.validate(text)
    if not validation["valid"]:
        reply = (
            "Dạ anh/chị vui lòng gửi số điện thoại di động đủ "
            "10 chữ số và đúng đầu số để kích hoạt bảo hành ạ."
        )
    else:
        phone = str(validation["phone"])
        _save_pending_activation_request(chat_id, phone)
        reply = (
            f"Dạ em đã ghi nhận số điện thoại {phone}. Anh/chị vui "
            "lòng gửi mã đơn hàng hoặc hình ảnh đơn hàng có hiển thị "
            "rõ mã đơn để em xác minh thông tin kích hoạt bảo hành ạ."
        )

    telegram_service.send_message(chat_id, reply)
    return True


def process_message(chat_id: int, text: str) -> None:
    started_at = time.perf_counter()
    replied = False

    try:
        if not telegram_ready():
            raise RuntimeError("Telegram service chưa sẵn sàng")

        assert telegram_service is not None

        if _handle_activation_message(chat_id, text):
            replied = True
        else:
            logger.info(
                "TELEGRAM MESSAGE IGNORED chat_id=%s "
                "reason=no_activation_entry",
                chat_id,
            )
    except Exception:
        logger.exception("TELEGRAM MESSAGE ERROR chat_id=%s", chat_id)
        if telegram_service is not None:
            telegram_service.send_message(
                chat_id,
                "Dạ hệ thống đang gián đoạn. Anh/chị vui lòng thử lại sau ạ.",
            )
    finally:
        logger.info(
            "BOT RESPONSE chat_id=%s replied=%s total=%.3fs",
            chat_id,
            replied,
            time.perf_counter() - started_at,
        )


def process_image(
    chat_id: int,
    file_id: str,
    caption: str | None = None,
) -> None:
    started_at = time.perf_counter()
    status = "processing"

    try:
        if not telegram_ready():
            raise RuntimeError("Telegram service chưa sẵn sàng")

        assert telegram_service is not None
        assert ai_service is not None

        pending = _peek_pending_activation_request(chat_id)
        if not pending:
            logger.info(
                "TELEGRAM IMAGE IGNORED chat_id=%s "
                "reason=no_activation_session",
                chat_id,
            )
            status = "ignored"
            return

        telegram_service.send_typing(chat_id)
        image_bytes, file_path = telegram_service.download_file(file_id)
        extension = os.path.splitext(file_path)[1].lower()
        mime_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(extension)

        if not mime_type:
            telegram_service.send_message(
                chat_id,
                "Dạ ảnh cần có định dạng JPG, PNG hoặc WEBP ạ.",
            )
            status = "invalid_format"
            return

        extracted = ai_service.extract_order_from_image(
            image_bytes=image_bytes,
            mime_type=mime_type,
        )
        order_code = extracted.get("order_code")
        order_confident = extracted.get("order_code_confident") is True

        if not order_code or not order_confident:
            reply = (
                "Dạ em chưa đọc rõ mã đơn trên hình ảnh. Anh/chị vui "
                "lòng gửi lại ảnh rõ hơn hoặc nhập trực tiếp mã đơn ạ."
            )
        else:
            phone = str(pending["phone"])
            reply = _verify_order_for_activation(
                chat_id,
                phone,
                str(order_code),
            )

        telegram_service.send_message(chat_id, reply)
        status = "replied"
    except Exception:
        status = "error"
        logger.exception("TELEGRAM IMAGE ERROR chat_id=%s", chat_id)
        if telegram_service is not None:
            telegram_service.send_message(
                chat_id,
                "Dạ em chưa thể xử lý ảnh lúc này. Anh/chị thử lại sau ạ.",
            )
    finally:
        logger.info(
            "BOT IMAGE RESPONSE chat_id=%s status=%s total=%.3fs",
            chat_id,
            status,
            time.perf_counter() - started_at,
        )


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

    chat_id, text, file_id = extract_message(payload)
    if chat_id is not None and file_id:
        background_tasks.add_task(
            process_image,
            chat_id,
            file_id,
            text,
        )
    elif chat_id is not None and text:
        background_tasks.add_task(process_message, chat_id, text)

    return {"status": "received"}
