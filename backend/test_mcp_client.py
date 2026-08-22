from fastmcp import Client
import asyncio

async def main():
    async with Client("http://localhost:8000/mcp") as client:
        list_tools = await client.list_tools()
        print(list_tools)
        tables = await client.call_tool("get_table_names")
        print(tables.data)
        print(tables.content[0].text)

if __name__ == '__main__':
    asyncio.run(main())