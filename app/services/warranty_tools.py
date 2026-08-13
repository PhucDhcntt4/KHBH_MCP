import logging
from typing import Any

from app.services.order_service import (
    OrderService,
)
from app.services.policy_service import PolicyService
from app.config import PHONE_PREFIX_PATH
from app.services.phone_validation_service import (
    PhoneValidationService,
)

phone_validator = PhoneValidationService(
    PHONE_PREFIX_PATH
)

order_service = OrderService()
policy_service = PolicyService()
logger = logging.getLogger(__name__)


def search_warranty_policy(
    question: str,
) -> dict[str, Any]:
    """
    Tra cứu chính sách đổi hàng và bảo hành chính thức.

    Dùng công cụ này trước khi trả lời mọi câu hỏi về điều kiện,
    trường hợp hỗ trợ, địa chỉ gửi hàng, chi phí hoặc thời gian
    xử lý đổi hàng/bảo hành.

    Args:
        question:
            Câu hỏi chính sách của khách hàng.

    Returns:
        Nội dung chính sách chính thức dùng để trả lời khách.
    """

    try:
        return policy_service.search(question)
    except Exception:
        logger.exception("Không thể đọc chính sách bảo hành")
        return {
            "success": False,
            "status": "policy_error",
            "content": "",
        }


def search_order(
    phone: str,
    order_code: str | None = None,
) -> dict[str, Any]:
    """
    Tìm đơn hàng của khách theo số điện thoại.

    Args:
        phone:
            Số điện thoại khách dùng khi đặt hàng.

        order_code:
            Mã đơn hàng nếu khách đã cung cấp.
            Tham số này có thể để trống.

    Returns:
        Kết quả tìm kiếm đơn hàng gồm trạng thái,
        số lượng đơn và danh sách đơn phù hợp.
    """

    try:
        phone_result = phone_validator.validate(phone)

        if not phone_result["valid"]:
            return {
                "success": False,
                "status": phone_result["status"],
                "message": (
                    "Số điện thoại chưa đúng quy định. Cần yêu cầu "
                    "khách cung cấp số di động Việt Nam đủ 10 chữ "
                    "số và có đầu số hợp lệ."
                ),
                "orders": [],
            }

        normalized_phone = str(phone_result["phone"])
        customer_exists = order_service.customer_exists(
            normalized_phone
        )

        if not order_code:
            if customer_exists:
                return {
                    "success": False,
                    "status": (
                        "customer_found_order_code_required"
                    ),
                    "message": (
                        "Đã xác minh số điện thoại có khách "
                        "hàng. Cần yêu cầu khách cung cấp "
                        "thêm mã đơn hàng."
                    ),
                    "orders": [],
                }

            return {
                "success": False,
                "status": "customer_not_found",
                "message": (
                    "Không tìm thấy khách hàng theo "
                    "số điện thoại được cung cấp."
                ),
                "orders": [],
            }
        
        orders = order_service.search(
            phone=normalized_phone,
            order_code=order_code,
        )

        if not orders:
            return {
                "success": False,
                "status": "order_not_found",
                "message": (
                    "Không tìm thấy mã đơn được cung cấp. "
                    "MCP hiện chưa dùng phone để ràng buộc đơn."
                ),
                "orders": [],
            }

        safe_orders = []

        for order in orders:
            safe_orders.append(
                {
                    "order_code": order.get("order_code"),
                    "order_status": order.get("order_status"),
                    "warranty_status": order.get(
                        "warranty_status",
                        "not_activated",
                    ),
                    "products": order.get("products", []),
                    "channel": order.get("channel"),
                }
            )

        return {
            "success": True,
            "status": "order_found",
            "count": len(safe_orders),
            "customer_found": customer_exists,
            "orders": safe_orders,
        }

    except Exception as error:
        logger.exception("Không thể tìm đơn hàng")
        return {
            "success": False,
            "status": "search_error",
            "message": str(error),
            "orders": [],
        }


def search_order_by_code(
    order_code: str,
) -> dict[str, Any]:
    """Tìm theo mã bằng get_order; MCP hiện chưa ràng buộc phone."""
    try:
        order = order_service.get_by_order_code(order_code)

        if not order:
            return {
                "success": False,
                "status": "order_code_not_found",
                "message": "Không tìm thấy mã đơn trong hệ thống.",
                "order": None,
            }

        return {
            "success": True,
            "status": "order_code_found",
            "order": {
                "order_code": order.get("order_code"),
                "channel": order.get("channel"),
                "has_phone": bool(order.get("phone")),
            },
        }
    except Exception as error:
        logger.exception("Không thể tìm đơn theo mã")
        return {
            "success": False,
            "status": "order_code_search_error",
            "message": str(error),
            "order": None,
        }


