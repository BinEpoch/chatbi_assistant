from fastmcp import FastMCP
from db_parser import DBParser
from typing import Annotated

# from fastmcp import FastMCP + from db_parser import DBParser
# 建一个全局 parser = DBParser("sqlite:///chinook.db")
# 建 mcp = FastMCP(name="...")
# 用 @mcp.tool 装饰器把 DBParser 的 4 个方法包成 MCP 工具：
# get_table_names() → return parser.get_table_names()
# get_table_fields(table_name) → return parser.get_table_fields(table_name)
# get_table_sample(table_name, limit=3) → return parser.get_table_sample(table_name, limit)
# execute_sql(sql) → return parser.execute_sql(sql)
# 每个 tool 写 docstring（MCP 会把 docstring 当 tool description 给 Agent 看）
# __main__ 里 mcp.run(transport="http", port=8000)

mcp = FastMCP(
    name="Chinook_MCP",
    instructions="chinook数据库相关集成mcp平台，提供查表、字段结果、查询sql语句、查表实例的工具"
)

parser = DBParser("sqlite:////Users/bin/Downloads/ai/test_demo/projects/chatbi_assistant/data/chinook.db")

@mcp.tool
def get_table_names() -> list:
    """获取所有的表名称"""
    return parser.get_table_names()

@mcp.tool
def get_table_fields(table_name: Annotated[str, "要查询的表名称"]) -> str:
    """查询对应表的字段信息"""
    return parser.get_table_fields(table_name)

@mcp.tool
def get_table_sample(table_name: Annotated[str, "要查询的表名称"], limit: Annotated[int, "数据示例的条数"] = 3) -> str:
    """查询对应表的数据示例"""
    return parser.get_table_sample(table_name,limit)

@mcp.tool
def execute_sql(sql: Annotated[str,"要执行的sql语句"]) -> str:
    """执行SQL语句，得到查询的结果"""
    return parser.execute_sql(sql)



if __name__ == '__main__':
    mcp.run(transport="http", port=8000)

