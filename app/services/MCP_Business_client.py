import asyncio
from typing import Any

from  fastmcp import Client # type: ignore

from app.config import (
    MCP_TIMEOUT,
    MCP_URL
)

class MCPBusinessClient:
    def __init__(self) -> None :
        if not MCP_URL:
            raise RuntimeError("thiếu mcp_url ")

        self.url = MCP_URL
        self.timeout = MCP_TIMEOUT

    async def _call_tool_async(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> Any:
        async with Client(self.url)  as client:
            return await asyncio.wait_for(
                client.call_tool(
                    name,
                    arguments,
                ),
                timeout= self.timeout
            )

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> Any:
        result = asyncio.run(
            self._call_tool_async(
                name=name,
                arguments=arguments
            )
        )

        data = getattr(result, "data", None)

        if data is not None:
            return data

        if hasattr(result, "model_dump"):
            return result.model_dump()

        raise RuntimeError(f"MCP tool{name} trả kết quả k hợp lệ")