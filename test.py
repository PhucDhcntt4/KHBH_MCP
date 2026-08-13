import asyncio
import json
from fastmcp import Client # type: ignore

MCP_URL = "http://168.144.105.210:8000/mcp"


async def main():
    async with Client(MCP_URL) as client:
        tools = await client.list_tools()

        for tool in tools:
            print(f"\n===== {tool.name} =====")

            print("\nINPUT:")
            print(json.dumps(
                tool.inputSchema,
                ensure_ascii=False,
                indent=4
            ))

            print("\nOUTPUT:")
            print(json.dumps(
                tool.outputSchema,
                ensure_ascii=False,
                indent=4
            ))


if __name__ == "__main__":
    asyncio.run(main())