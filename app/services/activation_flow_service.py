import logging
import os
import re
import threading
import time
import unicodedata
from typing import Any

from app.channels.base import MessageChannel
from app.services.AI.base import AIService
from app.services.activation_service import ActivationService
from app.services.order_service import OrderService, normalize_order_code
from app.services.phone_validation_service import PhoneValidationService


logger = logging.getLogger("uvicorn.error")

class ActivationFlowService:
    """Luồng nghiệp vụ kích hoạt bảo hành dùng chung cho nhiều channel."""

    def __init__(
        self,
        ai_service: AIService,
        order_service: OrderService,
        activation_service: ActivationService,
        phone_validator: PhoneValidationService,
    ) -> None:
        self.ai_service = ai_service
        self.order_service = order_service
        self.activation_service = activation_service
        self.phone_validator = phone_validator

        self._state_lock = threading.Lock()

        # State được tách theo (channel_name, conversation_id) để Telegram/Zalo
        # không đè trạng thái của nhau.
        self._pending_activation_requests: dict[
            tuple[str, str], dict[str, Any]
        ] = {}
        self._pending_confirmations: dict[
            tuple[str, str], dict[str, Any]
        ] = {}
        self._ready_activations: dict[
            tuple[str, str], dict[str, Any]
        ] = {}

        self._pending_ttl_seconds = 10 * 60

    @staticmethod
    def _conversation_key(
        channel: MessageChannel,
        conversation_id: str | int,
    ) -> tuple[str, str]:
        return channel.channel_name, str(conversation_id)

    @staticmethod
    def _mask_phone(phone: str) -> str:
        if len(phone) < 7:
            return "***"
        return f"{phone[:3]}****{phone[-3:]}"

    @staticmethod
    def _has_activation_keyword(text: str) -> bool:
        normalized = unicodedata.normalize("NFD", text.casefold())
        normalized = "".join(
            character
            for character in normalized
            if unicodedata.category(character) != "Mn"
        )
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return "kich hoat" in normalized and "bao hanh" in normalized

    @staticmethod
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

    def _save_pending_activation_request(
        self,
        channel: MessageChannel,
        conversation_id: str | int,
        phone: str | None,
        order_codes: list[str] | None = None,
    ) -> None:
        key = self._conversation_key(channel, conversation_id)
        with self._state_lock:
            current = self._pending_activation_requests.get(key, {})
            current.update({"created_at": time.monotonic()})
            if phone is not None:
                current["phone"] = phone
            else:
                current.setdefault("phone", None)
            if order_codes:
                current["order_codes"] = order_codes
            self._pending_activation_requests[key] = current

    def _peek_pending_activation_request(
        self,
        channel: MessageChannel,
        conversation_id: str | int,
    ) -> dict[str, Any] | None:
        key = self._conversation_key(channel, conversation_id)
        with self._state_lock:
            pending = self._pending_activation_requests.get(key)
            if not pending:
                return None

            if time.monotonic() - pending["created_at"] > self._pending_ttl_seconds:
                self._pending_activation_requests.pop(key, None)
                return None

            return dict(pending)

    def _clear_pending_activation_request(
        self,
        channel: MessageChannel,
        conversation_id: str | int,
    ) -> None:
        key = self._conversation_key(channel, conversation_id)
        with self._state_lock:
            self._pending_activation_requests.pop(key, None)

    def _save_pending_confirmation(
        self,
        channel: MessageChannel,
        conversation_id: str | int,
        phone: str,
        order: dict[str, Any],
        customer_exists: bool,
        phone_source: str,
    ) -> None:
        key = self._conversation_key(channel, conversation_id)
        with self._state_lock:
            self._pending_confirmations[key] = {
                "phone": phone,
                "order_code": order["order_code"],
                "external_order_id": order.get("external_order_id"),
                "channel": order.get("channel"),
                "customer_exists": customer_exists,
                "phone_source": phone_source,
                "created_at": time.monotonic(),
            }

    def _peek_pending_confirmation(
        self,
        channel: MessageChannel,
        conversation_id: str | int,
    ) -> dict[str, Any] | None:
        key = self._conversation_key(channel, conversation_id)
        with self._state_lock:
            pending = self._pending_confirmations.get(key)
            if not pending:
                return None
            if time.monotonic() - pending["created_at"] > self._pending_ttl_seconds:
                self._pending_confirmations.pop(key, None)
                return None
            return dict(pending)

    def _clear_pending_confirmation(
        self,
        channel: MessageChannel,
        conversation_id: str | int,
    ) -> None:
        key = self._conversation_key(channel, conversation_id)
        with self._state_lock:
            self._pending_confirmations.pop(key, None)

    def _ai_activation_response(
        self,
        event: str,
        context: dict[str, Any],
        customer_message: str | None = None,
    ) -> dict[str, str]:
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
                    "Dạ, anh/chị xác nhận dùng số điện thoại "
                    f"{context.get('phone')} có trên phiếu hàng để kích hoạt "
                    f"bảo hành cho đơn hàng {context.get('order_code')} đúng không ạ?"
                )
            else:
                reply = (
                    "Dạ, anh/chị xác nhận dùng số điện thoại "
                    f"{context.get('phone')} để kích hoạt bảo hành cho đơn "
                    f"hàng {context.get('order_code')} đúng không ạ?"
                )
            return {"intent": "unknown", "reply": reply}

        try:
            return self.ai_service.activation_conversation(
                event=event,
                context=context,
                customer_message=customer_message,
            )
        except Exception:
            logger.exception("ACTIVATION AI RESPONSE ERROR event=%s", event)
            fallbacks = {
                "activation_requested_without_phone": (
                    "Dạ, anh/chị vui lòng cung cấp số điện thoại kèm "
                    "mã đơn hàng, hoặc số điện thoại kèm hình ảnh "
                    "phiếu mua hàng/phiếu giao hàng để em hỗ trợ kích hoạt "
                    "bảo hành ạ."
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
                "phone_required_after_image": (
                    "Dạ em đã ghi nhận mã đơn từ phiếu hàng. Anh/chị "
                    "vui lòng cung cấp thêm số điện thoại để em tiếp "
                    "tục xác minh và kích hoạt bảo hành giúp mình ạ."
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
                "activation_succeeded": (
                    "Dạ, đơn hàng đã được kích hoạt bảo hành thành công ạ."
                ),
                "already_activated": (
                    f"Dạ, đơn hàng {context.get('order_code')} đã được "
                    "kích hoạt bảo hành trước đó rồi ạ."
                ),
                "activation_failed": (
                    "Dạ, hệ thống chưa thể kích hoạt bảo hành. "
                    "Anh/chị vui lòng thử lại sau ạ."
                ),
            }
            return {
                "intent": "unknown",
                "reply": fallbacks.get(
                    event,
                    "Dạ anh/chị vui lòng thử lại ạ.",
                ),
            }

    def _prepare_activation(
        self,
        channel: MessageChannel,
        conversation_id: str | int,
        phone: str,
        order: dict[str, Any],
    ) -> None:
        key = self._conversation_key(channel, conversation_id)
        with self._state_lock:
            self._ready_activations[key] = {
                "phone": phone,
                "order_code": order["order_code"],
                "external_order_id": order.get("external_order_id"),
                "channel": order.get("channel"),
                "status": "ready_for_activation",
                "created_at": time.time(),
            }

    def _verify_order_for_confirmation(
        self,
        channel: MessageChannel,
        conversation_id: str | int,
        phone: str,
        order_codes: list[str],
        phone_source: str = "customer_message",
    ) -> str:
        order: dict[str, Any] | None = None
        lookup_responded = False

        for order_code in order_codes:
            try:
                candidate_order = self.order_service.get_by_order_code(
                    order_code,
                    lookup_phone=phone,
                )
                lookup_responded = True
            except Exception:
                logger.exception(
                    "PRE ACTIVATION ORDER CANDIDATE ERROR channel=%s "
                    "conversation_id=%s order_code=%s",
                    channel.channel_name,
                    conversation_id,
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
            return self._ai_activation_response(
                "order_not_found",
                {"order_candidates": order_codes},
            )["reply"]

        try:
            customer_exists = self.order_service.customer_exists(phone)
        except Exception:
            logger.exception(
                "PRE ACTIVATION CUSTOMER LOOKUP ERROR channel=%s "
                "conversation_id=%s order_code=%s phone=%s",
                channel.channel_name,
                conversation_id,
                order["order_code"],
                self._mask_phone(phone),
            )
            return (
                "Dạ hệ thống kiểm tra thông tin khách hàng đang gián "
                "đoạn. Anh/chị vui lòng thử lại sau giúp em ạ."
            )

        self._save_pending_confirmation(
            channel,
            conversation_id,
            phone,
            order,
            customer_exists,
            phone_source,
        )
        self._clear_pending_activation_request(channel, conversation_id)

        context = {
            "phone": phone,
            "order_code": order["order_code"],
            "channel": order.get("channel") or "không xác định",
            "phone_source": phone_source,
        }
        logger.info(
            "ACTIVATION CONFIRMATION REQUIRED channel=%s conversation_id=%s "
            "order_code=%s channel_order=%s phone=%s customer_exists=%s",
            channel.channel_name,
            conversation_id,
            order["order_code"],
            order.get("channel") or "unknown",
            self._mask_phone(phone),
            customer_exists,
        )
        return self._ai_activation_response("order_verified", context)["reply"]

    def _verify_order_for_activation(
        self,
        channel: MessageChannel,
        conversation_id: str | int,
        phone: str,
        order_code: str,
    ) -> str:
        key = self._conversation_key(channel, conversation_id)

        try:
            order = self.order_service.get_by_order_code(
                order_code,
                lookup_phone=phone,
            )
        except Exception:
            logger.exception(
                "PRE ACTIVATION ORDER LOOKUP ERROR channel=%s "
                "conversation_id=%s order_code=%s",
                channel.channel_name,
                conversation_id,
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
            activation = self.activation_service.activate(
                chat_id=conversation_id,
                phone=phone,
                order_number=order["order_code"],
                channel=order.get("channel"),
                source_channel=channel.channel_name,
            )
        except Exception:
            logger.exception(
                "ACTIVATION ERROR channel=%s conversation_id=%s "
                "order_code=%s channel_order=%s phone=%s",
                channel.channel_name,
                conversation_id,
                order["order_code"],
                order.get("channel") or "unknown",
                self._mask_phone(phone),
            )
            return self._ai_activation_response(
                "activation_failed",
                activation_context,
            )["reply"]

        if activation["status"] == "already_activated":
            self._prepare_activation(channel, conversation_id, phone, order)
            with self._state_lock:
                self._ready_activations[key]["status"] = "already_activated"
                self._ready_activations[key]["request_id"] = activation[
                    "request_id"
                ]
            self._clear_pending_activation_request(channel, conversation_id)
            logger.info(
                "ACTIVATION ALREADY EXISTS channel=%s conversation_id=%s "
                "order_code=%s channel_order=%s phone=%s",
                channel.channel_name,
                conversation_id,
                order["order_code"],
                order.get("channel") or "unknown",
                self._mask_phone(phone),
            )
            return self._ai_activation_response(
                "already_activated",
                activation_context,
            )["reply"]

        if activation["status"] != "activated":
            logger.warning(
                "ACTIVATION REJECTED channel=%s conversation_id=%s "
                "order_code=%s channel_order=%s phone=%s",
                channel.channel_name,
                conversation_id,
                order["order_code"],
                order.get("channel") or "unknown",
                self._mask_phone(phone),
            )
            return self._ai_activation_response(
                "activation_failed",
                activation_context,
            )["reply"]

        self._prepare_activation(channel, conversation_id, phone, order)
        with self._state_lock:
            self._ready_activations[key]["status"] = "activated"
            self._ready_activations[key]["request_id"] = activation[
                "request_id"
            ]
        self._clear_pending_activation_request(channel, conversation_id)
        logger.info(
            "ACTIVATION SUCCESS channel=%s conversation_id=%s "
            "order_code=%s channel_order=%s phone=%s",
            channel.channel_name,
            conversation_id,
            order["order_code"],
            order.get("channel") or "unknown",
            self._mask_phone(phone),
        )
        return self._ai_activation_response(
            "activation_succeeded",
            activation_context,
        )["reply"]

    def _handle_activation_message(
        self,
        channel: MessageChannel,
        conversation_id: str | int,
        text: str,
    ) -> bool:
        key = self._conversation_key(channel, conversation_id)

        confirmation = self._peek_pending_confirmation(
            channel,
            conversation_id,
        )
        if confirmation:
            context = {
                "phone": confirmation["phone"],
                "order_code": confirmation["order_code"],
                "channel": confirmation.get("channel") or "không xác định",
                "phone_source": confirmation.get("phone_source"),
            }
            decision = self._ai_activation_response(
                "confirmation_received",
                context,
                customer_message=text,
            )
            intent = decision.get("intent", "unknown")

            if intent == "confirm":
                channel.send_typing(conversation_id)
                reply = self._verify_order_for_activation(
                    channel,
                    conversation_id,
                    str(confirmation["phone"]),
                    str(confirmation["order_code"]),
                )
                with self._state_lock:
                    activation_finished = key in self._ready_activations
                if activation_finished:
                    self._clear_pending_confirmation(channel, conversation_id)
            elif intent == "cancel":
                self._clear_pending_confirmation(channel, conversation_id)
                reply = decision["reply"]
            else:
                reply = decision["reply"]

            logger.info(
                "ACTIVATION CONFIRMATION channel=%s conversation_id=%s "
                "intent=%s order_code=%s channel_order=%s phone=%s",
                channel.channel_name,
                conversation_id,
                intent,
                confirmation["order_code"],
                confirmation.get("channel") or "unknown",
                self._mask_phone(str(confirmation["phone"])),
            )
            channel.send_message(conversation_id, reply)
            return True

        pending = self._peek_pending_activation_request(
            channel,
            conversation_id,
        )
        order_candidates = self._extract_order_candidates(text)
        validation = self.phone_validator.validate(text)

        if pending:
            if validation["valid"]:
                phone = str(validation["phone"])
                excluded = {phone, "SO" + phone}
                order_candidates = [
                    candidate
                    for candidate in order_candidates
                    if candidate not in excluded
                ]
                self._save_pending_activation_request(
                    channel,
                    conversation_id,
                    phone,
                )

                if not order_candidates:
                    order_candidates = list(pending.get("order_codes") or [])
                    if not order_candidates:
                        reply = self._ai_activation_response(
                            "phone_received",
                            {"phone": phone},
                            customer_message=text,
                        )["reply"]
                        channel.send_message(conversation_id, reply)
                        return True

            if order_candidates:
                available_phone = (
                    validation["phone"]
                    if validation["valid"]
                    else pending.get("phone")
                )
                if not available_phone:
                    self._save_pending_activation_request(
                        channel,
                        conversation_id,
                        None,
                        order_candidates,
                    )
                    reply = self._ai_activation_response(
                        "phone_required_after_image",
                        {"order_candidates": order_candidates},
                        customer_message=text,
                    )["reply"]
                    channel.send_message(conversation_id, reply)
                    return True

                phone = str(available_phone)
                channel.send_typing(conversation_id)
                reply = self._verify_order_for_confirmation(
                    channel,
                    conversation_id,
                    phone,
                    order_candidates,
                )
                channel.send_message(conversation_id, reply)
                return True

            if self._has_activation_keyword(text):
                if pending.get("phone"):
                    event = "phone_received"
                    context = {"phone": str(pending["phone"])}
                else:
                    event = "activation_requested_without_phone"
                    context = {}
                reply = self._ai_activation_response(
                    event,
                    context,
                    customer_message=text,
                )["reply"]
                channel.send_message(conversation_id, reply)
                return True

        if not validation["valid"]:
            status = validation.get("status")
            if status == "phone_missing":
                if not self._has_activation_keyword(text):
                    return False

                self._save_pending_activation_request(
                    channel,
                    conversation_id,
                    None,
                )
                reply = self._ai_activation_response(
                    "activation_requested_without_phone",
                    {},
                    customer_message=text,
                )["reply"]
                channel.send_message(conversation_id, reply)
                return True

            if status == "invalid_phone_length":
                event = "invalid_phone_length"
            else:
                event = "invalid_phone_prefix"

            reply = self._ai_activation_response(
                event,
                {"phone": validation.get("phone")},
                customer_message=text,
            )["reply"]
            channel.send_message(conversation_id, reply)
            return True

        phone = str(validation["phone"])
        excluded = {phone, "SO" + phone}
        order_candidates = [
            candidate
            for candidate in order_candidates
            if candidate not in excluded
        ]
        if order_candidates:
            channel.send_typing(conversation_id)
            reply = self._verify_order_for_confirmation(
                channel,
                conversation_id,
                phone,
                order_candidates,
            )
            channel.send_message(conversation_id, reply)
            return True

        self._save_pending_activation_request(
            channel,
            conversation_id,
            phone,
        )
        reply = self._ai_activation_response(
            "phone_received",
            {"phone": phone},
            customer_message=text,
        )["reply"]
        channel.send_message(conversation_id, reply)
        return True

    def process_message(
        self,
        channel: MessageChannel,
        conversation_id: str | int,
        text: str,
    ) -> None:
        started_at = time.perf_counter()
        replied = False

        try:
            if self._handle_activation_message(
                channel,
                conversation_id,
                text,
            ):
                replied = True
            else:
                logger.info(
                    "%s MESSAGE IGNORED conversation_id=%s "
                    "reason=no_valid_phone_or_pending_order",
                    channel.channel_name.upper(),
                    conversation_id,
                )
        except Exception:
            logger.exception(
                "%s MESSAGE ERROR conversation_id=%s",
                channel.channel_name.upper(),
                conversation_id,
            )
            channel.send_message(
                conversation_id,
                "Dạ hệ thống đang gián đoạn. Anh/chị vui lòng thử lại sau ạ.",
            )
        finally:
            logger.info(
                "BOT RESPONSE channel=%s conversation_id=%s replied=%s total=%.3fs",
                channel.channel_name,
                conversation_id,
                replied,
                time.perf_counter() - started_at,
            )

    def process_image(
        self,
        channel: MessageChannel,
        conversation_id: str | int,
        file_id: str,
        caption: str | None = None,
    ) -> None:
        started_at = time.perf_counter()
        status = "processing"

        try:
            # Ảnh có thể là đầu vào đầu tiên của luồng kích hoạt.
            pending = self._peek_pending_activation_request(
                channel,
                conversation_id,
            ) or {}

            channel.send_typing(conversation_id)
            image_bytes, file_path = channel.download_image(file_id)
            extension = os.path.splitext(file_path)[1].lower()
            mime_type = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
            }.get(extension)

            if not mime_type:
                channel.send_message(
                    conversation_id,
                    "Dạ ảnh cần có định dạng JPG, PNG hoặc WEBP ạ.",
                )
                status = "invalid_format"
                return

            extracted = self.ai_service.extract_order_from_image(
                image_bytes=image_bytes,
                mime_type=mime_type,
            )
            order_code = extracted.get("order_code")
            order_confident = extracted.get("order_code_confident") is True

            if not order_code or not order_confident:
                logger.info(
                    "%s IMAGE IGNORED conversation_id=%s "
                    "reason=no_confident_order_code",
                    channel.channel_name.upper(),
                    conversation_id,
                )
                status = "ignored"
                return

            extracted_phone = extracted.get("phone")
            masked_phone = extracted.get("masked_phone")
            phone_source = "customer_message"

            if extracted.get("phone_confident") is True and extracted_phone:
                phone = str(extracted_phone)
                phone_source = "order_image"
            elif (
                extracted.get("masked_phone_confident") is True
                and masked_phone
            ):
                phone = str(masked_phone).replace("x", "*").replace("X", "*")
                phone_source = "order_image"
            else:
                phone = pending.get("phone")

            if not phone:
                self._save_pending_activation_request(
                    channel,
                    conversation_id,
                    None,
                    [str(order_code)],
                )
                reply = self._ai_activation_response(
                    "phone_required_after_image",
                    {"order_candidates": [str(order_code)]},
                )["reply"]
                channel.send_message(conversation_id, reply)
                status = "replied"
                return

            reply = self._verify_order_for_confirmation(
                channel,
                conversation_id,
                str(phone),
                [str(order_code)],
                phone_source=phone_source,
            )

            channel.send_message(conversation_id, reply)
            status = "replied"
        except Exception:
            status = "error"
            logger.exception(
                "%s IMAGE ERROR conversation_id=%s",
                channel.channel_name.upper(),
                conversation_id,
            )
            channel.send_message(
                conversation_id,
                "Dạ em chưa thể xử lý ảnh lúc này. Anh/chị thử lại sau ạ.",
            )
        finally:
            logger.info(
                "BOT IMAGE RESPONSE channel=%s conversation_id=%s "
                "status=%s total=%.3fs",
                channel.channel_name,
                conversation_id,
                status,
                time.perf_counter() - started_at,
            )
