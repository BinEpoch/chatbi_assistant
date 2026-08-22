"""方案B：用 langchain-mcp-adapters 库自动把 MCP 工具转成 LangChain tools"""
from fastmcp import Client
from langchain_mcp_adapters.tools import load_mcp_tools
import asyncio

# TypeError: ClientToolsMixin.list_tools() got an unexpected keyword argument 'cursor'
# langchain_mcp_adapters版本不兼容

MCP_URL = "http://localhost:8000/mcp"

async def main():
    """启动时调一次，把 MCP 工具自动转成 LangChain tools"""
    async with Client(MCP_URL) as client:
        tools = await load_mcp_tools(client)
    return tools

if __name__ == '__main__':
    tools = asyncio.run(main())
    for item in tools:
        print(f"\n=== {item.name} ===")
        print(f"  description: {item.description[:60] if item.description else 'None'}")
        print(f"  args_schema: {item.args_schema}")