import os
import asyncio
from agents.mcp.server import MCPServerStreamableHttp
from agents import Agent, Runner, set_default_openai_api, set_tracing_disabled
set_default_openai_api("chat_completions")
set_tracing_disabled(True)

# 起 mcp_service.py（后台跑 8000 端口）
# 用 openai-agents 的 Agent + MCPServer 接上 http://127.0.0.1:8000/mcp
# Agent.runner.run("统计每个客户的总消费金额") —— Agent 自己决定调哪几个 tool
# 打印 Agent 的 final answer

os.environ["OPENAI_API_KEY"] = "22026200394732511280"
os.environ["OPENAI_BASE_URL"] = "https://aigc.sankuai.com/v1/openai/native"


async def main():

    async with MCPServerStreamableHttp(
        name="mcp",
        params={"url": "http://localhost:8000/mcp"}
    ) as server:
        agent = Agent(
            name="数据小助手",
            model="gpt-4o-mini",
            instructions="""
            你是一个数据小助手，可以根据用户提的问题选择数据工具得到结果，然后再对结果整理反馈给用户
            1.拿到问题前，先看下有多少张表，找到匹配的表，必须找到至少一个，get_table_names工具可以看所有的表名称
            2.查询涉及表的字段信息和数据示例，get_table_fields工具查看字段信息，get_table_sample工具查看字段样例
            3.根据用户的提问写出执行的sql，要用真实的字段和表，然后使用execute_sql工具执行sql得出结果数据
            4.根据结果数据整理成用户易懂的文字返回
            """,
            mcp_servers=[server],
        )
        response =await Runner.run(agent, "统计每个客户的总消费金额")
        # print(response)
        print(response.final_output)


if __name__ == '__main__':
    asyncio.run(main())
