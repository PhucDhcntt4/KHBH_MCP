import asyncio
import json
from fastmcp import Client # type: ignore


MCP_URL = "http://168.144.105.210:8000/mcp"


def print_json(title: str, data) -> None:
    print(f"\n===== {title} =====")
    print(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=4,
            default=str
        )
    )


async def main():
    async with Client(MCP_URL) as client:

        # 1. Lấy thông tin khách hàng
        customer_result = await client.call_tool(
            "get_customer",
            {
                "phone": "0867091188",
            }
        )

        customer_data = getattr(customer_result, "data", None)

        if customer_data is None:
            customer_data = customer_result.model_dump()

        print_json("GET CUSTOMER", customer_data)

        # 2. Lấy thông tin đơn hàng
        order_result = await client.call_tool(
            "get_order",
            {
                "phone": "0867091188",
                "order_number": "SO0003159",
            }
        )

        order_data = getattr(order_result, "data", None)

        if order_data is None:
            order_data = order_result.model_dump()

        print_json("GET ORDER", order_data)

        # 4. Kích hoạt bảo hành cho đơn hàng.
        # Lưu ý: tool này ghi dữ liệu thật lên hệ thống MCP.
        # Hãy thay bằng phone và order_number dùng cho kiểm thử
        # trước khi chạy file.
        activation_result = await client.call_tool(
            "activate_order",
            {
                "phone": "0867091188",
                "order_number": "SO0003159",
            },
        )

        activation_data = getattr(
            activation_result,
            "data",
            None,
        )

        if activation_data is None:
            activation_data = activation_result.model_dump()

        print_json("ACTIVATE ORDER", activation_data)


if __name__ == "__main__":
    asyncio.run(main())
