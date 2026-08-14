import re
from typing import Any

from app.services.MCP_Business_client import (
    MCPBusinessClient
)


# MCP hiện bắt buộc nhận phone nhưng chưa dùng phone để lọc đơn.
# Chỉ dùng giá trị này cho thao tác tra cứu đơn theo order_number.
ORDER_LOOKUP_PLACEHOLDER_PHONE = "0900000000"


def normalize_phone(
    value: str | None,
) -> str | None:
    if not value:
        return None

    phone = re.sub(r"\D", "", value)

    return phone or None 


def is_valid_phone(value: str | None) -> bool:
    """Lớp chặn cuối: MCP chỉ nhận phone đã chuẩn hóa đủ 10 số."""
    phone = normalize_phone(value)

    return bool(phone and len(phone) == 10)


def normalize_order_code(
    value: str | None,
) -> str | None:
    if not value:
        return None

    order_code = re.sub(r"\s+", "", value).upper()

    # Khách thường nhập nhầm chữ O thành số 0: S0... -> SO...
    if re.fullmatch(r"S0\d+", order_code):
        order_code = "SO" + order_code[2:]

    return order_code


class OrderService:
    def __init__(
        self,
        client: MCPBusinessClient | None = None,
    ) -> None:
        self.client = client or MCPBusinessClient()

    def customer_exists(
        self,
        phone: str,
    ) -> bool:
        normalized_phone = normalize_phone(phone)

        if not is_valid_phone(normalized_phone):
            return False

        result = self.client.call_tool(
            "get_customer",
            {
                "phone": normalized_phone,
            },
        )

        if not isinstance(result, dict):
            return False

        if result.get("status") is not True:
            return False

        customers = result.get("datas")

        return (
            isinstance(customers, list)
            and any(
                isinstance(customer, dict)
                and normalize_phone(
                    customer.get("phone")
                )
                == normalized_phone
                for customer in customers
            )
        )

    def search(
        self,
        phone: str,
        order_code: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_phone = normalize_phone(phone)
        normalized_code = normalize_order_code(
            order_code
        )

        if not is_valid_phone(normalized_phone):
            return []

        # get_order bắt buộc phải có order_number.
        if not normalized_code:
            return []

        result = self.client.call_tool(
            "get_order",
            {
                "phone": normalized_phone,
                "order_number": normalized_code,
            },
        )

        if not isinstance(result, dict):
            return []

        if result.get("status") is not True:
            return []

        data = result.get("datas")

        found = (
            isinstance(data, dict)
            and data.get("id") is not None
        )

        if not found:
            return []

        return [
            {
                "order_code": normalized_code,
                "phone": normalized_phone,
                "external_order_id": data.get("id"),
                "channel": data.get("channel"),
                "external_code": data.get("code"),
                "order_status": "verified",
                "warranty_status": "unknown",
                "products": [],
            }
        ]

    def get_by_order_code(
        self,
        order_code: str,
        lookup_phone: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_code = normalize_order_code(order_code)

        if not normalized_code:
            return None

        result = self.client.call_tool(
            "get_order",
            {
                "phone": (
                    normalize_phone(lookup_phone)
                    if is_valid_phone(lookup_phone)
                    else ORDER_LOOKUP_PLACEHOLDER_PHONE
                ),
                "order_number": normalized_code,
            },
        )

        if not isinstance(result, dict):
            return None

        if result.get("status") is not True:
            return None

        data = result.get("datas")

        if not isinstance(data, dict) or data.get("id") is None:
            return None

        return {
            "order_code": (
                normalize_order_code(data.get("order_number"))
                or normalized_code
            ),
            "phone": normalize_phone(data.get("phone")),
            "external_order_id": data.get("id"),
            "channel": data.get("channel"),
            "external_code": data.get("code"),
        }
