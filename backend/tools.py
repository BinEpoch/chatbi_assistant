import os

from fastmcp import Client
from langchain.tools import tool
from typing import Annotated
import asyncio
from langgraph.types import interrupt

MCP_URL = os.getenv("MCP_URL", "http://localhost:8000/mcp")

@tool
async def list_tables() -> str:
    """
    获取数据库中的所有表名，需要找表时调用该工具
    """
    async with Client(MCP_URL) as client:
        result =await client.call_tool("get_table_names", {})
    return str(result.data)

@tool
async def describe_table(table_name: Annotated[str, "表名称"]) -> str:
    """
    查询指定表的字段结构，需要看表结构（字段名称、类型、结构）是调用该工具
    """
    async with Client(MCP_URL) as client:
        result =await client.call_tool("get_table_fields", {"table_name": table_name})
    return str(result.data)

@tool
async def get_table_sample(table_name: Annotated[str, "表名称"], limit: Annotated[int, "示例数据条数"] = 3) -> str:
    """
    查询指标表的示例数据，需要进一步探查表真实数据，调用该工具
    """
    async with Client(MCP_URL) as client:
        result =await client.call_tool("get_table_sample", {"table_name": table_name, "limit": limit})
    return str(result.data)

@tool
async def execute_sql(sql: Annotated[str, "要执行的sql语句"]) -> dict:
    """
    执行SQL查询语句并返回结果。
    什么时候调：当你已经用 list_tables 看了表名、describe_table 看了字段后，根据用户问题生成SQL并执行。
    SQL规范：
    1. 仅支持 SELECT 或 WITH 开头的查询语句
    2. 禁止多语句（不要用分号拼接多条SQL）
    3. 禁止 DELETE/INSERT/UPDATE/DROP 等破坏性操作
    4. 查询结果建议加 LIMIT 限制行数
    参数：
    sql: 要执行的SQL语句
    """
    user_decision = interrupt({"sql": sql})
    if user_decision != "yes":
        return "用户拒绝执行 SQL"
    async with Client(MCP_URL) as client:
        result =await client.call_tool("execute_sql", {"sql": sql})
    return result.data












