import asyncio

from tools import list_tables, describe_table, get_table_sample, execute_sql
from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command


_agent = None
checkpointer = InMemorySaver()

def _get_agent():
    global _agent
    if _agent is None:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            base_url="https://aigc.sankuai.com/v1/openai/native",
            api_key="22026200394732511280"
        )
        _agent = create_agent(
            model=llm,
            system_prompt="""
                你是一个专业的数据小助手，可以根据用户提的问题选择数据工具得到结果，然后再对结果整理反馈给用户
                1.拿到问题前，先看下有多少张表，找到匹配的表，必须找到至少一个，list_tables工具可以看所有的表名称
                2.查询涉及表的字段信息和数据示例，describe_table工具查看字段信息，get_table_sample工具查看字段样例
                3.根据用户的提问写出执行的sql，要用真实的字段和表，然后使用execute_sql工具执行sql得出结果数据
                4.根据结果数据整理成用户易懂的文字返回
                5.输出规范：
                - 先用中文列出查询结果（如"Helena Holý - $49.62"）
                - 只要查询包含聚合（COUNT/SUM/AVG/MAX/MIN）或 GROUP BY
                必须在回答最末尾追加图表数据块，格式：
                  ```chart
                  {"type": "bar", "x_label": "客户", "y_label": "消费金额", "data": [{"x": "Helena Holý", "y": 49.62}, {"x": "Richard", "y": 47.62}]}
                  ```
                  data 取前 10 条即可（超过 10 条截断）
                  type 默认 bar；时间序列（按日期分组）用 line；占比类用 pie
                - 非聚合查询（如"有哪些表"、查单值）不追加
                - chart 块必须在回答最末尾，和正文之间不留空行之外的任何内容
                - chart 块用 ```chart 标记，禁止用 ```json
                """,
            tools=[list_tables, describe_table, get_table_sample, execute_sql],
            checkpointer=checkpointer,
        )
    return _agent

async def chat(question :str, thread_id: str) -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    result =await _get_agent().ainvoke(
        {"messages": [HumanMessage(content=question)]},
        config
    )

    return {"answer": result["messages"][-1].content, "interrupted": False}

async def resume(thread_id: str, confirmation: str = "yes") -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    result = await _get_agent().ainvoke(
        Command(resume=confirmation),
        config
    )

    return {"answer": result["messages"][-1].content, "interrupted": False}


if __name__ == '__main__':
    r1 = asyncio.run(chat("统计每个客户的总消费金额", "test_thread_1"))
    print(r1)