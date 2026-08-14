import logging
import os
import re
import threading
import time
import unicodedata
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
from app.services.order_service import OrderService, normalize_order_code
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
_pending_confirmations: dict[int, dict[str, Any]] = {}
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

def _has_activation_keyword(text: str) -> bool:
    normalized = unicodedata.normalize("NFD", text.casefold())
    normalized = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return "kich hoat" in normalized and "bao hanh" in normalized


def _extract_order_candidates(text: str) -> list[str]:
    tokens = re.findall(
        r"\b[A-Za-z0-9][A-Za-z0-9-]{4,}\b",
        text,
    )
    candidates: list[str] = []

    for token in tokens:
        value = token.strip().upper()
        if not any(character.isdigit() for character in value):
            continue

        normalized = normalize_order_code(value)
        if normalized:
            candidates.append(normalized)

        # Khách có thể chỉ gửi phần số, MCP lại lưu mã đủ SO.
        if value.isdigit():
            candidates.append("SO" + value)

    return list(dict.fromkeys(candidates))


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


def _save_pending_confirmation(
    chat_id: int,
    phone: str,
    order: dict[str, Any],
    customer_exists: bool,
    phone_source: str,
) -> None:
    with _state_lock:
        _pending_confirmations[chat_id] = {
            "phone": phone,
            "order_code": order["order_code"],
            "external_order_id": order.get("external_order_id"),
            "channel": order.get("channel"),
            "customer_exists": customer_exists,
            "phone_source": phone_source,
            "created_at": time.monotonic(),
        }


def _peek_pending_confirmation(chat_id: int) -> dict[str, Any] | None:
    with _state_lock:
        pending = _pending_confirmations.get(chat_id)
        if not pending:
            return None
        if time.monotonic() - pending["created_at"] > _PENDING_TTL_SECONDS:
            _pending_confirmations.pop(chat_id, None)
            return None
        return dict(pending)


def _clear_pending_confirmation(chat_id: int) -> None:
    with _state_lock:
        _pending_confirmations.pop(chat_id, None)


def _ai_activation_response(
    event: str,
    context: dict[str, Any],
    customer_message: str | None = None,
) -> dict[str, str]:
    assert ai_service is not None

    # Kết quả nghiệp vụ này cần câu trả lời cố định,
    # không cho model tự thêm yêu cầu hoặc hướng dẫn.
    if event == "already_activated":
        return {
            "intent": "unknown",
            "reply": (
                f"Dạ, đơn hàng {context.get('order_code')} đã được "
                "kích hoạt bảo hành trước đó rồi ạ."
            ),
        }

    if event == "order_verified":
        if context.get("phone_source") == "order_image":
            reply = (
                f"Dạ, anh/chị xác nhận dùng số điện thoại "
                f"{context.get('phone')} có trên phiếu hàng để kích hoạt "
                f"bảo hành cho đơn hàng {context.get('order_code')} đúng không ạ?"
            )
        else:
            reply = (
                f"Dạ, anh/chị xác nhận dùng số điện thoại "
                f"{context.get('phone')} để kích hoạt bảo hành cho đơn "
                f"hàng {context.get('order_code')} đúng không ạ?"
            )
        return {
            "intent": "unknown",
            "reply": reply,
        }

    try:
        return ai_service.activation_conversation(
            event=event,
            context=context,
            customer_message=customer_message,
        )
    except Exception:
        logger.exception("ACTIVATION AI RESPONSE ERROR event=%s", event)
        fallbacks = {
            "activation_requested_without_phone": (
                "Dạ, anh/chị vui lòng cung cấp số điện thoại "
                "dùng để kích hoạt bảo hành giúp em ạ."
            ),
            "phone_received": (
                f"Dạ em đã ghi nhận số điện thoại {context.get('phone')}. "
                "Anh/chị vui lòng gửi mã đơn hoặc ảnh đơn hàng giúp em với ạ."
            ),
            "invalid_phone_length": (
                "Dạ số điện thoại cần có đúng 10 chữ số. "
                "Anh/chị vui lòng kiểm tra và gửi lại giúp em ạ."
            ),
            "invalid_phone_prefix": (
                "Dạ đầu số điện thoại chưa hợp lệ. "
                "Anh/chị vui lòng kiểm tra và gửi lại giúp em ạ."
            ),
            "order_not_found": (
                "Dạ em chưa tìm thấy mã đơn từ thông tin anh/chị "
                "vừa gửi. Anh/chị vui lòng gửi lại mã đơn hoặc hình "
                "ảnh phiếu mua hàng/phiếu giao hàng để em kiểm tra lại ạ."
            ),
            "order_verified": (
                f"Dạ em đã xác minh mã đơn {context.get('order_code')} "
                f"với số điện thoại {context.get('phone')}. Anh/chị "
                "xác nhận thông tin trên đúng không ạ?"
            ),
            "confirmation_received": (
                "Dạ anh/chị vui lòng trả lời XÁC NHẬN nếu thông "
                "tin đúng, hoặc HỦY nếu cần thay đổi ạ."
            ),
            "activation_succeeded": "Dạ, đơn hàng đã được kích hoạt bảo hành thành công ạ.",
            "already_activated": (
                f"Dạ, đơn hàng {context.get('order_code')} đã được "
                "kích hoạt bảo hành trước đó rồi ạ."
            ),
            "activation_failed": "Dạ, hệ thống chưa thể kích hoạt bảo hành. Anh/chị vui lòng thử lại sau ạ.",
            "activation_requested_without_phone": (
                "Dạ, anh/chị vui lòng cung cấp số điện thoại "
                "dùng để kích hoạt bảo hành giúp em ạ."
            ),
        }
        return {"intent": "unknown", "reply": fallbacks.get(event, "Dạ anh/chị vui lòng thử lại ạ.")}


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


def _verify_order_for_confirmation(
    chat_id: int,
    phone: str,
    order_codes: list[str],
    phone_source: str = "customer_message",
) -> str:
    order: dict[str, Any] | None = None
    lookup_responded = False

    for order_code in order_codes:
        try:
            candidate_order = order_service.get_by_order_code(
                order_code,
                lookup_phone=phone,
            )
            lookup_responded = True
        except Exception:
            logger.exception(
                "PRE ACTIVATION ORDER CANDIDATE ERROR order_code=%s",
                order_code,
            )
            continue

        if candidate_order:
            order = candidate_order
            break

    if not order and not lookup_responded:
        return (
            "Dạ hệ thống xác minh đơn hàng đang gián đoạn. "
            "Anh/chị vui lòng thử lại sau giúp em ạ."
        )

    if not order:
        return _ai_activation_response(
            "order_not_found",
            {"order_candidates": order_codes},
        )["reply"]

    try:
        customer_exists = order_service.customer_exists(phone)
    except Exception:
        logger.exception(
            "PRE ACTIVATION CUSTOMER LOOKUP ERROR order_code=%s phone=%s",
            order["order_code"],
            _mask_phone(phone),
        )
        return (
            "Dạ hệ thống kiểm tra thông tin khách hàng đang gián "
            "đoạn. Anh/chị vui lòng thử lại sau giúp em ạ."
        )

    _save_pending_confirmation(
        chat_id,
        phone,
        order,
        customer_exists,
        phone_source,
    )
    _clear_pending_activation_request(chat_id)
    context = {
        "phone": phone,
        "order_code": order["order_code"],
        "channel": order.get("channel") or "không xác định",
        "phone_source": phone_source,
    }
    logger.info(
        "ACTIVATION CONFIRMATION REQUIRED chat_id=%s order_code=%s "
        "channel=%s phone=%s customer_exists=%s",
        chat_id,
        order["order_code"],
        order.get("channel") or "unknown",
        _mask_phone(phone),
        customer_exists,
    )
    return _ai_activation_response("order_verified", context)["reply"]


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

    activation_context = {
        "phone": phone,
        "order_code": order["order_code"],
        "channel": order.get("channel") or "không xác định",
    }
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
        return _ai_activation_response(
            "activation_failed",
            activation_context,
        )["reply"]

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
        return _ai_activation_response(
            "already_activated",
            activation_context,
        )["reply"]

    if activation["status"] != "activated":
        logger.warning(
            "ACTIVATION REJECTED chat_id=%s order_code=%s channel=%s phone=%s",
            chat_id,
            order["order_code"],
            order.get("channel") or "unknown",
            _mask_phone(phone),
        )
        return _ai_activation_response(
            "activation_failed",
            activation_context,
        )["reply"]

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
    return _ai_activation_response(
        "activation_succeeded",
        activation_context,
    )["reply"]


def _handle_activation_message(chat_id: int, text: str) -> bool:
    assert telegram_service is not None

    confirmation = _peek_pending_confirmation(chat_id)
    if confirmation:
        context = {
            "phone": confirmation["phone"],
            "order_code": confirmation["order_code"],
            "channel": confirmation.get("channel") or "không xác định",
            "phone_source": confirmation.get("phone_source"),
        }
        decision = _ai_activation_response(
            "confirmation_received",
            context,
            customer_message=text,
        )
        intent = decision.get("intent", "unknown")

        if intent == "confirm":
            telegram_service.send_typing(chat_id)
            reply = _verify_order_for_activation(
                chat_id,
                str(confirmation["phone"]),
                str(confirmation["order_code"]),
            )
            with _state_lock:
                activation_finished = chat_id in _ready_activations
            if activation_finished:
                _clear_pending_confirmation(chat_id)
        elif intent == "cancel":
            _clear_pending_confirmation(chat_id)
            reply = decision["reply"]
        else:
            reply = decision["reply"]

        logger.info(
            "ACTIVATION CONFIRMATION chat_id=%s intent=%s order_code=%s "
            "channel=%s phone=%s",
            chat_id,
            intent,
            confirmation["order_code"],
            confirmation.get("channel") or "unknown",
            _mask_phone(str(confirmation["phone"])),
        )
        telegram_service.send_message(chat_id, reply)
        return True

    pending = _peek_pending_activation_request(chat_id)
    order_candidates = _extract_order_candidates(text)
    validation = phone_validator.validate(text)

    if pending:
        if validation["valid"]:
            phone = str(validation["phone"])
            excluded = {phone, "SO" + phone}
            order_candidates = [
                candidate
                for candidate in order_candidates
                if candidate not in excluded
            ]
            _save_pending_activation_request(chat_id, phone)

            if not order_candidates:
                reply = _ai_activation_response(
                    "phone_received",
                    {"phone": phone},
                    customer_message=text,
                )["reply"]
                telegram_service.send_message(chat_id, reply)
                return True

        if order_candidates:
            phone = str(
                validation["phone"]
                if validation["valid"]
                else pending["phone"]
            )
            telegram_service.send_typing(chat_id)
            reply = _verify_order_for_confirmation(
                chat_id,
                phone,
                order_candidates,
            )
            telegram_service.send_message(chat_id, reply)
            return True

        if _has_activation_keyword(text):
            reply = _ai_activation_response(
                "phone_received",
                {"phone": str(pending["phone"])},
                customer_message=text,
            )["reply"]
            telegram_service.send_message(chat_id, reply)
            return True

    if not validation["valid"]:
        status = validation.get("status")
        if status == "phone_missing":
            if not _has_activation_keyword(text):
                return False

            reply = _ai_activation_response(
                "activation_requested_without_phone",
                {},
                customer_message=text,
            )["reply"]
            telegram_service.send_message(chat_id, reply)
            return True
        if status == "invalid_phone_length":
            event = "invalid_phone_length"
        else:
            event = "invalid_phone_prefix"
        reply = _ai_activation_response(
            event,
            {"phone": validation.get("phone")},
            customer_message=text,
        )["reply"]
        telegram_service.send_message(chat_id, reply)
        return True

    phone = str(validation["phone"])
    excluded = {phone, "SO" + phone}
    order_candidates = [
        candidate
        for candidate in order_candidates
        if candidate not in excluded
    ]
    if order_candidates:
        telegram_service.send_typing(chat_id)
        reply = _verify_order_for_confirmation(
            chat_id,
            phone,
            order_candidates,
        )
        telegram_service.send_message(chat_id, reply)
        return True

    _save_pending_activation_request(chat_id, phone)
    reply = _ai_activation_response(
        "phone_received",
        {"phone": phone},
        customer_message=text,
    )["reply"]

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
                "reason=no_valid_phone_or_pending_order",
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
            extracted_phone = extracted.get("phone")
            masked_phone = extracted.get("masked_phone")

            if extracted.get("phone_confident") is True and extracted_phone:
                phone = str(extracted_phone)
            elif (
                extracted.get("masked_phone_confident") is True
                and masked_phone
            ):
                phone = str(masked_phone).replace("x", "*").replace("X", "*")
            else:
                phone = str(pending["phone"])

            reply = _verify_order_for_confirmation(
                chat_id,
                phone,
                [str(order_code)],
                phone_source="order_image",
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
