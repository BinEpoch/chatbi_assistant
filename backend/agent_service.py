import aiosqlite
import asyncio
from pydantic import BaseModel, ValidationError
from typing import Literal, List, Optional
import json

from tools import list_tables, describe_table, get_table_sample, execute_sql
from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.types import Command
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


class ChartItem(BaseModel):
    x: str
    y: float

class ChartData(BaseModel):
    type: Literal["bar", "line", "pie"]
    x_label: str
    y_label: str
    data: List[ChartItem]

class AgentResponse(BaseModel):
    """统一响应格式"""
    text: str
    chart: Optional[ChartData] = None

_agent = None

async def _get_agent(checkpointer):
    """接收外部传入的 checkpointer,不自己创建"""
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
                5.输出规范(结构化)：
                - text 字段: 用中文列出查询结果
                - chart 字段：【强制要求】当 SQL 包含 COUNT/SUM/AVG/MAX/MIN 或 GROUP BY 时，chart 字段【必须】填充完整结构化数据，【禁止】填 null！
                  type: bar(默认)/line(时间序列)/pie(占比)
                  x_label: x轴标签(维度名)
                  y_label: y轴标签(指标名)
                  data: [{"x": "维度值", "y": 数值}, ...] 必须填入 execute_sql 工具返回的真实结果，前 20 条
                - 只有非聚合查询(查单值、查表名、查字段)时，chart 才填 null
                【重要】如果用户问的是统计/汇总类问题，chart 绝对不能为 null，必须把查询结果填进 data 数组
                """,
            tools=[list_tables, describe_table, get_table_sample, execute_sql],
            checkpointer=checkpointer,
            response_format=AgentResponse,
        )
    return _agent

async def chat(question :str, thread_id: str, checkpointer) -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    # 轮次上限:超过 20 条消息(10轮)只保留最近 10 条 + 第一条
    agent =await _get_agent(checkpointer)
    # 取当前状态
    state = await agent.aget_state(config)
    messages = state.values.get("messages",[]) if state else []
    if len(messages) > 20:
        # 从倒数第 10 条往后找,找到一个 HumanMessage 作为切点
        cut = 10
        while cut < len(messages):
            msg = messages[-cut]
            if msg.__class__.__name__ == "HumanMessage":
                break
            cut += 1
        # 简单裁剪:留第一条 + 最近 10 条
        trimmed = [messages[0]] + messages[-cut:]
        # 用 aupdate_state 把裁剪后的消息写回
        await agent.aupdate_state(config, {"messages": trimmed})

    result =await agent.ainvoke(
        {"messages": [HumanMessage(content=question)]},
        config
    )
    state = await agent.aget_state(config)
    if state and state.next:
        messages = state.values.get("messages",[])
        if messages[-1].tool_calls:
            sql = messages[-1].tool_calls[0].get("args", {}).get("sql", "")
            return {"answer": sql, "interrupted": True}
    # 拿到最终消息
    last_msg = result["messages"][-1]
    # content 是 JSON 字符串,用 Pydantic 解析
    try:
        response = AgentResponse.model_validate_json(last_msg.content)
        chart = response.chart
        if chart is None:
            chart = _build_chart_from_messages(result["messages"])
        return {"text": response.text, "chart": chart, "interrupted": False}
    except ValidationError:
        return {"text": last_msg.content, "chart": None, "interrupted": False}

async def resume(thread_id: str, checkpointer, confirmation: str = "yes") -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    agent = await _get_agent(checkpointer)
    result = await agent.ainvoke(
        Command(resume=confirmation),
        config
    )
    last_msg = result["messages"][-1]
    try:
        response = AgentResponse.model_validate_json(last_msg.content)
        chart = response.chart
        if chart is None:
            chart = _build_chart_from_messages(result["messages"])
        return {"text": response.text, "chart": chart, "interrupted": False}
    except ValidationError:
        return {"text": last_msg.content, "chart": None, "interrupted": False}

def _build_chart_from_messages(messages) -> Optional[ChartData]:
    """chart 兜底:LLM 没给 chart 时,从 execute_sql 结果自动构造"""
    # 1. 倒序找最后一个调 execute_sql 的 AIMessage
    sql = None
    tool_msg_idx = None
    for i in range(len(messages)-1, -1, -1):
        msg = messages[i]
        if msg.__class__.__name__ == "AIMessage" and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                if tc.get("name") == "execute_sql":
                    sql = tc["args"].get("sql", "")
                    tool_msg_idx = i + 1
                    break
            if sql:
                break
    # 没找到 execute_sql,不兜底
    if not sql:
        return None
    # 2. 检查 SQL 是否含聚合关键词(不区分大小写)
    sql_upper = sql.upper()
    agg_keywords = ["COUNT", "SUM", "AVG", "MAX", "MIN", "GROUP BY"]
    if not any(kw in sql_upper for kw in agg_keywords):
        return None # 非聚合查询,不该有图
    # 3. 取 ToolMessage,解析 columns/rows
    #    从 messages[tool_msg_idx].content 解析
    cont = messages[tool_msg_idx].content
    #    提示:content 是 JSON 字符串,用 json.loads
    cont_js = json.loads(cont)
    #    解析出 columns(list) 和 rows(list of list)
    columns = cont_js.get("columns", "")
    rows = cont_js.get("rows", "")
    # 4. 构造 ChartData
    #    第一列当 x(字符串),最后一列当 y(float)
    #    rows 取前 20 条
    #    type 默认 "bar"
    #    x_label 用第一列列名,y_label 用最后一列列名
    #    返回 ChartData 对象
    x_label = columns[0]
    y_label = columns[-1]
    items = []
    for row in rows[:20]:
        items.append(ChartItem(x=str(row[0]), y=float(row[-1])))
    return ChartData(type="bar", x_label=x_label, y_label=y_label, data=items)


if __name__ == '__main__':
    # async def main():
    #     # 用 async with 管理连接生命周期
    #     async with aiosqlite.connect("checkpoints.db") as conn:
    #         checkpointer = AsyncSqliteSaver(conn=conn)
    #         await checkpointer.setup()
    #         r1 = await chat("统计每个客户的总消费金额", "test_thread_1", checkpointer)
    #         print(r1)
    # asyncio.run(main())



    # async def main():
    #     async with aiosqlite.connect("checkpoints.db") as conn:
    #         checkpointer = AsyncSqliteSaver(conn=conn)
    #         await checkpointer.setup()
    #         r1 = await chat("统计每个客户的总消费金额", "test_thread_1", checkpointer)
    #         print("第一次:", r1["answer"][:50])
    #         r2 = await chat("刚才我问的是什么?", "test_thread_1", checkpointer)  # 同一个 thread_id
    #         print("第二次:", r2["answer"][:50])

    async def main():
        async with aiosqlite.connect("checkpoints.db") as conn:
            checkpointer = AsyncSqliteSaver(conn=conn)
            await checkpointer.setup()
            # 第一次:问问题,Agent 调 execute_sql 时会暂停
            r1 = await chat("统计每个客户的总消费金额", "test_thread_1", checkpointer)
            print("第一次 interrupted:", r1["interrupted"])
            print("第一次 SQL:", r1.get("answer",""))
            # 第二次:用户确认,resume 继续
            r2 = await resume("test_thread_1", checkpointer, "yes")
            print("第二次 interrupted:", r2["interrupted"])
            print("第二次 text:", r2.get("text", "")[:80])
            print("第二次 chart:", r2.get("chart", {}))


    asyncio.run(main())