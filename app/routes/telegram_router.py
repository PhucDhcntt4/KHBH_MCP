import logging
import os
import re
import threading
import time
from collections import deque
from typing import Any

from fastapi import ( # type: ignore
    APIRouter,
    BackgroundTasks,
    HTTPException,
    Request,
)

from app.services.AI.base import AIService
from app.services.order_service import OrderService, normalize_phone
from app.services.telegram_service import TelegramService
from app.services.warranty_tools import (
    phone_validator,
    search_order,
)
router = APIRouter(
    prefix="/api/telegram",
    tags=["Telegram"],
)

logger = logging.getLogger("uvicorn.error")
logger.setLevel(logging.INFO)

ai_service: AIService | None = None
telegram_service: TelegramService | None = None
order_service = OrderService()

_state_lock = threading.Lock()
_histories: dict[int, deque[dict[str, str]]] = {}
_processed_updates: deque[int] = deque(maxlen=1000)
_processed_update_set: set[int] = set()
_pending_order_lookups: dict[int, dict[str, Any]] = {}
_pending_phone_confirmations: dict[int, dict[str, Any]] = {}
_confirmed_order_phones: dict[str, dict[str, Any]] = {}
_PENDING_TTL_SECONDS = 10 * 60


def configure_telegram(
    warranty_agent: AIService,
) -> None:
    global ai_service, telegram_service
    ai_service = warranty_agent
    telegram_service = TelegramService()


def telegram_ready() -> bool:
    return (
        ai_service is not None
        and telegram_service is not None
    )


def _save_pending_order_lookup(
    chat_id: int,
    order: dict[str, Any] | None = None,
    order_code: str | None = None,
) -> bool:
    if order is None and order_code:
        order = order_service.get_by_order_code(order_code)

    if not order or not order.get("order_code"):
        return False

    with _state_lock:
        _pending_order_lookups[chat_id] = {
            "order_code": order["order_code"],
            "external_order_id": order.get("external_order_id"),
            "channel": order.get("channel"),
            "stored_phone": order.get("phone"),
            "created_at": time.monotonic(),
        }

    return True


def _peek_pending_order_lookup(
    chat_id: int,
) -> dict[str, Any] | None:
    with _state_lock:
        pending = _pending_order_lookups.get(chat_id)

        if not pending:
            return None

        age = time.monotonic() - pending["created_at"]

        if age > _PENDING_TTL_SECONDS:
            _pending_order_lookups.pop(chat_id, None)
            return None

        return dict(pending)


def _clear_pending_order_lookup(chat_id: int) -> None:
    with _state_lock:
        _pending_order_lookups.pop(chat_id, None)


def _save_phone_confirmation(
    chat_id: int,
    order_code: str,
    channel: str,
    phone: str,
) -> None:
    with _state_lock:
        _pending_phone_confirmations[chat_id] = {
            "order_code": order_code,
            "channel": channel,
            "phone": phone,
            "created_at": time.monotonic(),
        }


def _peek_phone_confirmation(
    chat_id: int,
) -> dict[str, Any] | None:
    with _state_lock:
        pending = _pending_phone_confirmations.get(chat_id)

        if not pending:
            return None

        if time.monotonic() - pending["created_at"] > _PENDING_TTL_SECONDS:
            _pending_phone_confirmations.pop(chat_id, None)
            return None

        return dict(pending)


def _clear_phone_confirmation(chat_id: int) -> None:
    with _state_lock:
        _pending_phone_confirmations.pop(chat_id, None)


def _is_marketplace_channel(channel: str | None) -> bool:
    normalized = (channel or "").strip().upper()

    return any(
        marker in normalized
        for marker in ("SÀN", "TIKTOK", "SHOPEE", "LAZADA")
    )


def _confirmation_intent(text: str) -> str:
    normalized = text.strip().casefold()

    if normalized in {
        "xác nhận", "xac nhan", "đồng ý", "dong y",
        "ok", "oke", "yes", "đúng", "dung",
    }:
        return "confirm"

    if normalized in {
        "hủy", "huy", "không", "khong", "sai", "cancel",
    }:
        return "cancel"

    return "unknown"


def _mask_phone(phone: str) -> str:
    if len(phone) < 7:
        return "***"

    return f"{phone[:3]}****{phone[-3:]}"


def _extract_order_code(text: str) -> str | None:
    match = re.search(
        r"\b[A-Za-z]{1,5}\d{5,}\b",
        text,
    )

    return match.group(0).upper() if match else None


def _extract_phone(text: str) -> str | None:
    candidates = re.findall(
        r"(?<!\d)(?:\+?84|0)(?:[\s.\-]*\d){9}(?!\d)",
        text,
    )

    for candidate in candidates:
        validation = phone_validator.validate(candidate)

        if validation["valid"]:
            return str(validation["phone"])

    return None


def _handle_structured_lookup(chat_id: int, text: str) -> bool:
    """Xử lý chắc chắn luồng mã đơn -> thu thập số điện thoại."""
    assert telegram_service is not None

    phone_confirmation = _peek_phone_confirmation(chat_id)

    if phone_confirmation:
        intent = _confirmation_intent(text)
        order_code = str(phone_confirmation["order_code"])
        phone = str(phone_confirmation["phone"])
        channel = str(phone_confirmation["channel"])

        if intent == "confirm":
            with _state_lock:
                _confirmed_order_phones[order_code] = {
                    "order_code": order_code,
                    "channel": channel,
                    "phone": phone,
                    "confirmed_at": time.time(),
                }
            _clear_phone_confirmation(chat_id)
            logger.info(
                "MARKETPLACE PHONE CONFIRMED order_code=%s "
                "channel=%s phone=%s",
                order_code,
                channel,
                _mask_phone(phone),
            )
            reply = (
                f"Dạ em đã ghi nhận số điện thoại {_mask_phone(phone)} "
                f"cho mã đơn {order_code} ạ."
            )
        elif intent == "cancel":
            _clear_phone_confirmation(chat_id)
            reply = (
                f"Dạ em đã hủy ghi nhận số điện thoại cho mã đơn "
                f"{order_code}. Anh/chị có thể gửi lại số đúng giúp em ạ."
            )
        else:
            reply = (
                f"Dạ anh/chị vui lòng trả lời XÁC NHẬN nếu số điện "
                f"thoại {_mask_phone(phone)} đúng, hoặc HỦY để nhập lại ạ."
            )

        telegram_service.send_message(chat_id, reply)
        _append_history(chat_id, text, reply)
        return True

    pending = _peek_pending_order_lookup(chat_id)
    new_order_code = _extract_order_code(text)

    # Khách có thể đổi sang mã đơn khác trong lúc bot đang chờ phone.
    # Mã đơn mới phải được ưu tiên, không được đưa qua validator phone.
    if pending and new_order_code:
        _clear_pending_order_lookup(chat_id)
        pending = None

    if pending:
        validation = phone_validator.validate(text)
        status = validation["status"]

        if not validation["valid"]:
            reply = (
                "Dạ số điện thoại cần có đúng 10 chữ số và đầu số "
                "di động hợp lệ. Anh/chị vui lòng kiểm tra rồi gửi "
                "lại giúp em ạ."
            )
        else:
            supplied_phone = str(validation["phone"])
            stored_phone = pending.get("stored_phone")
            order_code = str(pending["order_code"])
            channel = str(pending.get("channel") or "unknown")

            if stored_phone and stored_phone != supplied_phone:
                reply = (
                    f"Dạ số điện thoại {_mask_phone(supplied_phone)} "
                    f"chưa khớp với mã đơn {order_code}. Anh/chị "
                    "vui lòng kiểm tra và gửi lại giúp em ạ."
                )
            elif _is_marketplace_channel(channel):
                _clear_pending_order_lookup(chat_id)
                _save_phone_confirmation(
                    chat_id=chat_id,
                    order_code=order_code,
                    channel=channel,
                    phone=supplied_phone,
                )
                logger.info(
                    "MARKETPLACE PHONE WAITING CONFIRMATION "
                    "order_code=%s channel=%s phone=%s",
                    order_code,
                    channel,
                    _mask_phone(supplied_phone),
                )
                reply = (
                    f"Dạ anh/chị xác nhận số điện thoại "
                    f"{_mask_phone(supplied_phone)} dùng cho mã đơn "
                    f"{order_code} là đúng nhé. Anh/chị trả lời "
                    "XÁC NHẬN để em ghi nhận hoặc HỦY để nhập lại ạ."
                )
            else:
                customer_result = search_order(
                    phone=supplied_phone,
                    order_code=None,
                )
                status = customer_result.get("status")

                if status == "search_error":
                    reply = (
                        "Dạ hệ thống tra cứu khách hàng đang gián đoạn. "
                        "Anh/chị vui lòng thử lại sau giúp em ạ."
                    )
                    telegram_service.send_message(chat_id, reply)
                    _append_history(chat_id, text, reply)
                    return True

                customer_found = (
                    status == "customer_found_order_code_required"
                )
                _clear_pending_order_lookup(chat_id)
                logger.info(
                    "ORDER PHONE COLLECTED order_code=%s channel=%s "
                    "phone=%s customer_found=%s",
                    order_code,
                    channel,
                    _mask_phone(supplied_phone),
                    customer_found,
                )

                if customer_found:
                    reply = (
                        f"Dạ em đã xác minh số điện thoại "
                        f"{_mask_phone(supplied_phone)} có thông tin "
                        f"khách hàng và đã ghi nhận cho mã đơn "
                        f"{order_code} ạ."
                    )
                else:
                    reply = (
                        f"Dạ em đã ghi nhận số điện thoại "
                        f"{_mask_phone(supplied_phone)} cho mã đơn "
                        f"{order_code}. Hiện số này chưa có thông tin "
                        "khách hàng trong hệ thống ạ."
                    )

        telegram_service.send_message(chat_id, reply)
        _append_history(chat_id, text, reply)
        return True

    order_code = new_order_code

    if not order_code:
        return False

    telegram_service.send_typing(chat_id)
    supplied_phone = _extract_phone(text)

    try:
        order = order_service.get_by_order_code(
            order_code,
            lookup_phone=supplied_phone,
        )
    except Exception:
        logger.exception(
            "ORDER CODE LOOKUP ERROR order_code=%s",
            order_code,
        )
        reply = (
            "Dạ hệ thống tra cứu mã đơn đang gián đoạn. "
            "Anh/chị vui lòng thử lại sau giúp em ạ."
        )
    else:
        if not order:
            reply = (
                f"Dạ em chưa tìm thấy mã đơn {order_code} trong hệ "
                "thống. Anh/chị vui lòng kiểm tra lại mã đơn giúp em ạ."
            )
        else:
            logger.info(
                "ORDER CODE FOUND order_code=%s channel=%s "
                "has_phone=%s",
                order["order_code"],
                order.get("channel") or "unknown",
                bool(order.get("phone")),
            )

            if supplied_phone and _is_marketplace_channel(
                order.get("channel")
            ):
                channel = str(order.get("channel") or "unknown")
                _save_phone_confirmation(
                    chat_id=chat_id,
                    order_code=str(order["order_code"]),
                    channel=channel,
                    phone=supplied_phone,
                )
                logger.info(
                    "MARKETPLACE PHONE WAITING CONFIRMATION "
                    "order_code=%s channel=%s phone=%s",
                    order["order_code"],
                    channel,
                    _mask_phone(supplied_phone),
                )
                reply = (
                    f"Dạ anh/chị xác nhận số điện thoại "
                    f"{_mask_phone(supplied_phone)} dùng cho mã đơn "
                    f"{order['order_code']} là đúng nhé. Anh/chị trả "
                    "lời XÁC NHẬN để em ghi nhận hoặc HỦY để nhập lại ạ."
                )
                telegram_service.send_message(chat_id, reply)
                _append_history(chat_id, text, reply)
                return True

            if supplied_phone:
                customer_result = search_order(
                    phone=supplied_phone,
                    order_code=None,
                )
                customer_found = (
                    customer_result.get("status")
                    == "customer_found_order_code_required"
                )
                logger.info(
                    "ORDER PHONE COLLECTED order_code=%s channel=%s "
                    "phone=%s customer_found=%s",
                    order["order_code"],
                    order.get("channel") or "unknown",
                    _mask_phone(supplied_phone),
                    customer_found,
                )
                customer_text = (
                    "có thông tin khách hàng"
                    if customer_found
                    else "chưa có thông tin khách hàng"
                )
                reply = (
                    f"Dạ em đã tìm thấy mã đơn {order['order_code']} "
                    f"trong hệ thống. Số điện thoại "
                    f"{_mask_phone(supplied_phone)} {customer_text} ạ."
                )
            else:
                _save_pending_order_lookup(chat_id, order)
                reply = (
                    f"Dạ em đã tìm thấy mã đơn {order['order_code']} "
                    "trong hệ thống. Anh/chị vui lòng gửi số điện thoại "
                    "đặt hàng để em kiểm tra thông tin khách hàng nhé."
                )

    telegram_service.send_message(chat_id, reply)
    _append_history(chat_id, text, reply)
    return True


def _agent_reply(
    chat_id: int,
    event: str,
    fallback: str,
) -> str:
    if ai_service is None:
        return fallback

    try:
        return ai_service.compose_reply(
            event=event,
            history=_get_history(chat_id),
        )
    except Exception:
        logger.exception(
            "Không thể tạo câu trả lời AI chat_id=%s",
            chat_id,
        )
        return fallback


def _remember_update(update_id: Any) -> bool:
    if not isinstance(update_id, int):
        return True

    with _state_lock:
        if update_id in _processed_update_set:
            return False

        if len(_processed_updates) == _processed_updates.maxlen:
            oldest = _processed_updates[0]
            _processed_update_set.discard(oldest)

        _processed_updates.append(update_id)
        _processed_update_set.add(update_id)
        return True


def _get_history(chat_id: int) -> list[dict[str, str]]:
    with _state_lock:
        return list(_histories.get(chat_id, ()))


def _append_history(
    chat_id: int,
    user_text: str,
    model_text: str,
) -> None:
    with _state_lock:
        history = _histories.setdefault(
            chat_id,
            deque(maxlen=10),
        )
        history.append({"role": "user", "text": user_text})
        history.append({"role": "model", "text": model_text})

def extract_message(
    payload: dict[str, Any],
) -> tuple[int | None, str | None, str | None]:

    message = payload.get("message")

    if not isinstance(message, dict):
        return None, None, None

    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    text = message.get("text")
    caption = message.get("caption")
    photos = message.get("photo") or []

    file_id = None

    if photos:
        file_id = photos[-1].get("file_id")

    if chat_id is None:
        return None, None, None

    return (
        int(chat_id),
        str(text or caption or "").strip() or None,
        file_id,
    )

def process_message(
    chat_id: int,
    text: str,
) -> None:
    started_at = time.perf_counter()
    ai_seconds = 0.0
    send_seconds = 0.0
    replied = False

    try:
        if not telegram_ready():
            raise RuntimeError(
                "Telegram service chưa sẵn sàng"
            )

        assert telegram_service is not None
        assert ai_service is not None

        if _handle_structured_lookup(chat_id, text):
            replied = True
            return

        pending_lookup = _peek_pending_order_lookup(chat_id)
        supplied_phone = normalize_phone(text)

        if (
            pending_lookup
            and supplied_phone
            and 9 <= len(supplied_phone) <= 11
        ):
            order_code = str(
                pending_lookup["order_code"]
            )
            telegram_service.send_typing(chat_id)

            lookup_result = search_order(
                phone=supplied_phone,
                order_code=order_code,
            )

            if (
                lookup_result.get("success") is True
                and lookup_result.get("count") == 1
            ):
                _clear_pending_order_lookup(chat_id)
                reply = (
                    f"Dạ em đã xác minh được số điện thoại "
                    f"{supplied_phone} và mã đơn {order_code} có "
                    "tồn tại trong hệ thống ạ. Hiện em chưa có "
                    "đủ dữ liệu để kết luận trạng thái "
                    "bảo hành của đơn này."
                )
                logger.info(
                    "PENDING IMAGE ORDER VERIFIED chat_id=%s "
                    "order_code=%s",
                    chat_id,
                    order_code,
                )
            else:
                reply = (
                    f"Dạ số điện thoại {supplied_phone} chưa "
                    f"khớp với mã đơn {order_code}. Anh/chị "
                    "vui lòng kiểm tra và gửi lại số điện "
                    "thoại đặt hàng giúp em ạ."
                )

            send_started_at = time.perf_counter()
            telegram_service.send_message(chat_id, reply)
            send_seconds = (
                time.perf_counter() - send_started_at
            )
            replied = True
            _append_history(chat_id, text, reply)
            return

        telegram_service.send_typing(chat_id)

        ai_started_at = time.perf_counter()

        result = ai_service.chat(
            message=text,
            customer_id=f"telegram:{chat_id}",
            history=_get_history(chat_id),
        )

        ai_seconds = (
            time.perf_counter() - ai_started_at
        )

        reply = result.get("reply")

        if not reply:
            reply = (
                "Dạ hiện tại em chưa thể xử lý yêu cầu. "
                "Anh/chị vui lòng thử lại sau ạ."
            )

        send_started_at = time.perf_counter()

        telegram_service.send_message(
            chat_id=chat_id,
            text=reply,
        )

        send_seconds = (
            time.perf_counter() - send_started_at
        )
        replied = True

        _append_history(chat_id, text, reply)

    except Exception:
        logger.exception(
            "TELEGRAM MESSAGE ERROR chat_id=%s",
            chat_id,
        )

        try:
            if telegram_service is not None:
                telegram_service.send_message(
                    chat_id=chat_id,
                    text=(
                        "Dạ hệ thống đang gặp chút gián đoạn. "
                        "Anh/chị vui lòng thử lại sau ít phút ạ."
                    ),
                )
        except Exception:
            logger.exception(
                "Không thể gửi thông báo lỗi về Telegram"
            )

    finally:
        total_seconds = (
            time.perf_counter() - started_at
        )

        logger.info(
            "BOT RESPONSE chat_id=%s replied=%s "
            "provider=%s model=%s ai=%.3fs send=%.3fs total=%.3fs",
            chat_id,
            replied,
            (
                ai_service.provider_name
                if ai_service is not None
                else "unknown"
            ),
            (
                ai_service.model
                if ai_service is not None
                else "unknown"
            ),
            ai_seconds,
            send_seconds,
            total_seconds,
        )


def process_image(
    chat_id: int,
    file_id: str,
    caption: str | None = None,
) -> None:
    started_at = time.perf_counter()
    download_seconds = 0.0
    extraction_seconds = 0.0
    status = "processing"

    try:
        if not telegram_ready():
            raise RuntimeError("Telegram service chưa sẵn sàng")

        assert telegram_service is not None
        assert ai_service is not None

        telegram_service.send_typing(chat_id)

        download_started_at = time.perf_counter()
        image_bytes, file_path = telegram_service.download_file(
            file_id
        )
        download_seconds = (
            time.perf_counter() - download_started_at
        )

        extension = os.path.splitext(file_path)[1].lower()
        mime_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }
        mime_type = mime_types.get(extension)

        if not mime_type:
            telegram_service.send_message(
                chat_id,
                "Dạ ảnh cần có định dạng JPG, PNG hoặc WEBP ạ.",
            )
            return

        extraction_started_at = time.perf_counter()
        extracted = ai_service.extract_order_from_image(
            image_bytes=image_bytes,
            mime_type=mime_type,
        )
        extraction_seconds = (
            time.perf_counter() - extraction_started_at
        )
        status = "replied"
        phone = extracted.get("phone")
        masked_phone = extracted.get("masked_phone")
        order_code = extracted.get("order_code")
        phone_confident = (
            extracted.get("phone_confident") is True
        )
        masked_phone_confident = (
            extracted.get("masked_phone_confident") is True
        )
        order_code_confident = (
            extracted.get("order_code_confident") is True
        )
        confident = (
            phone_confident
            and order_code_confident
        )

        if (
            not phone
            and masked_phone
            and masked_phone_confident
            and order_code
            and order_code_confident
        ):
            order_saved = _save_pending_order_lookup(
                chat_id=chat_id,
                order_code=order_code,
            )

            if not order_saved:
                telegram_service.send_message(
                    chat_id,
                    (
                        f"Dạ em chưa tìm thấy mã đơn {order_code} "
                        "trong hệ thống. Anh/chị vui lòng kiểm tra "
                        "lại mã đơn giúp em ạ."
                    ),
                )
                return

            telegram_service.send_message(
                chat_id,
                (
                    f"Dạ em đọc được mã đơn {order_code} và số điện thoại "
                    f"bị che {masked_phone}. Anh/chị vui lòng gửi số điện "
                    "thoại đặt hàng đầy đủ để em xác minh đơn giúp mình ạ."
                ),
            )
            return

        if (
            order_code
            and order_code_confident
            and (not phone or not phone_confident)
        ):
            order_saved = _save_pending_order_lookup(
                chat_id=chat_id,
                order_code=order_code,
            )

            if not order_saved:
                telegram_service.send_message(
                    chat_id,
                    (
                        f"Dạ em chưa tìm thấy mã đơn {order_code} "
                        "trong hệ thống. Anh/chị vui lòng kiểm tra "
                        "lại mã đơn giúp em ạ."
                    ),
                )
                return

            reply = (
                f"Dạ em đã đọc được mã đơn {order_code}. Trên ảnh chưa "
                "có số điện thoại đặt hàng, anh/chị gửi số điện thoại "
                "giúp em để em xác minh đơn hàng nhé. 😊"
            )
            telegram_service.send_message(chat_id, reply)
            _append_history(
                chat_id,
                (
                    "Khách gửi ảnh kích hoạt bảo hành. Hệ thống đã đọc "
                    f"chắc chắn mã đơn {order_code}, nhưng ảnh không có "
                    "số điện thoại."
                ),
                reply,
            )
            return

        if (
            phone
            and phone_confident
            and (not order_code or not order_code_confident)
        ):
            reply = (
                f"Dạ em đã đọc được số điện thoại {phone}, nhưng chưa "
                "đọc rõ mã đơn. Anh/chị gửi thêm mã đơn hoặc ảnh có mã "
                "đơn rõ hơn giúp em nhé. 😊"
            )
            telegram_service.send_message(chat_id, reply)
            _append_history(
                chat_id,
                (
                    "Khách gửi ảnh kích hoạt bảo hành. Hệ thống đã đọc "
                    f"chắc chắn số điện thoại {phone}, nhưng chưa đọc "
                    "được mã đơn."
                ),
                reply,
            )
            return

        if not phone or not order_code or not confident:
            fallback = (
                "Dạ em chưa đọc rõ số điện thoại hoặc mã đơn. "
                "Anh/chị vui lòng gửi ảnh rõ và đầy đủ hơn ạ."
            )
            telegram_service.send_message(
                chat_id,
                _agent_reply(
                    chat_id,
                    (
                        "Không đọc chắc chắn được đầy đủ số điện "
                        "thoại và mã đơn từ ảnh khách gửi."
                    ),
                    fallback,
                ),
            )
            return

        search_result = search_order(
            phone=phone,
            order_code=order_code,
        )

        if (
            not search_result.get("success")
            or search_result.get("count") != 1
        ):
            telegram_service.send_message(
                chat_id,
                (
                    f"Dạ em chưa tìm thấy mã đơn {order_code} trong hệ "
                    "thống. Anh/chị vui lòng kiểm tra lại mã đơn giúp em ạ."
                ),
            )
            return

            fallback = (
                "Dạ thông tin trong ảnh chưa khớp với đơn hàng. "
                "Anh/chị vui lòng kiểm tra và gửi lại ảnh ạ."
            )
            telegram_service.send_message(
                chat_id,
                _agent_reply(
                    chat_id,
                    (
                        "Số điện thoại và mã đơn đọc từ ảnh không "
                        "khớp duy nhất một đơn hàng."
                    ),
                    fallback,
                ),
            )
            return

        customer_found = search_result.get("customer_found") is True
        customer_text = (
            "có thông tin khách hàng"
            if customer_found
            else "chưa có thông tin khách hàng"
        )
        telegram_service.send_message(
            chat_id,
            (
                f"Dạ em đã xác minh mã đơn {order_code} tồn tại trong "
                f"hệ thống. Số điện thoại {_mask_phone(phone)} "
                f"{customer_text} ạ. Hiện hệ thống chưa có dữ liệu "
                "trạng thái bảo hành."
            ),
        )
        return

        fallback = (
            f"Dạ em đã xác minh mã đơn {order_code} và số điện thoại "
            f"{_mask_phone(phone)} có tồn tại trong hệ thống ạ. Hiện "
            "hệ thống chưa cung cấp trạng thái kích hoạt bảo hành."
        )
        telegram_service.send_message(
            chat_id,
            _agent_reply(
                chat_id,
                (
                    f"Đã đọc và xác minh được đơn {order_code}, "
                    f"số điện thoại {_mask_phone(phone)}. Chỉ được "
                    "thông báo cặp dữ liệu tồn tại; chưa có dữ liệu "
                    "về trạng thái kích hoạt bảo hành."
                ),
                fallback,
            ),
        )

    except Exception:
        status = "error"
        logger.exception(
            "TELEGRAM IMAGE ERROR chat_id=%s",
            chat_id,
        )
        if telegram_service is not None:
            try:
                telegram_service.send_message(
                    chat_id,
                    (
                        "Dạ em chưa thể xử lý ảnh lúc này. "
                        "Anh/chị vui lòng thử lại sau ạ."
                    ),
                )
            except Exception:
                logger.exception(
                    "Không thể gửi lỗi xử lý ảnh về Telegram"
                )

    finally:
        total_seconds = (
            time.perf_counter() - started_at
        )

        logger.info(
            "BOT IMAGE RESPONSE chat_id=%s status=%s "
            "provider=%s model=%s download=%.3fs "
            "extraction=%.3fs total=%.3fs",
            chat_id,
            status,
            (
                ai_service.provider_name
                if ai_service is not None
                else "unknown"
            ),
            (
                ai_service.model
                if ai_service is not None
                else "unknown"
            ),
            download_seconds,
            extraction_seconds,
            total_seconds,
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
        background_tasks.add_task(
            process_message,
            chat_id,
            text,
        )

    return {
        "status": "received",
    }
