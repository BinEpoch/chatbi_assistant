"""
ChatBI 助手 - Streamlit 前端
功能：对话式查 Chinook 数据库（通过 FastAPI 后端）
"""

import uuid
import requests
import json
import re
import pandas as pd


API_URL = "http://localhost:8090"
HEADERS = {"X-API-Key": "sk-chatbi-dev-123456"}

import streamlit as st


# ============ 初始化 session_state ============
# st.session_state 是 Streamlit 的"全局状态"，跨 rerun 保持
if "messages" not in st.session_state:
    # messages 存对话历史，每条形如 {"role": "user"/"assistant", "content": "..."}
    st.session_state.messages = []

if "thread_id" not in st.session_state:
    # thread_id 随机生成一次，整个会话用同一个（让 checkpointer 能恢复 HITL 状态）
    st.session_state.thread_id = str(uuid.uuid4())

if "awaiting_confirmation" not in st.session_state:
    # 标记当前是否在等用户确认 SQL（HITL 暂停状态）
    st.session_state.awaiting_confirmation = False

if "pending_sql" not in st.session_state:
    # 暂存的待确认 SQL 文本
    st.session_state.pending_sql = None


# ============ 页面配置 ============
st.title("ChatBI 助手")
st.caption(f"会话ID: {st.session_state.thread_id}  |  自然语言查 Chinook 数据库")


# ============ 渲染对话历史 ============
# 每次用户输入或点按钮，Streamlit 会 rerun 整个脚本
# 所以要用 session_state.messages 重建对话
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])


# ============ HITL 确认区（如果在等用户确认） ============
if st.session_state.awaiting_confirmation:
    # 显示待确认的 SQL
    st.warning("LLM 生成了以下 SQL，请确认是否执行：")
    st.code(st.session_state.pending_sql, language="sql")

    # 两个按钮：确认 / 拒绝
    col1, col2 = st.columns(2)
    with col1:
        if st.button("确认执行", type="primary"):
            # 调 resume 恢复执行
            with st.spinner("正在执行 SQL..."):
                resp =requests.post(f"{API_URL}/resume", json={"thread_id": st.session_state.thread_id, "confirmation": "yes"}, timeout=120, headers=HEADERS)
            # 把结果加到对话历史
            result = resp.json()
            # 展示结果
            text = result.get("text", "")
            chart = result.get("chart")
            st.session_state.messages.append(
                {"role": "user", "content": "确认执行"}
            )
            st.session_state.messages.append(
                {"role": "assistant", "content": text}
            )
            st.write(text)
            if chart:
                if hasattr(chart, "model_dump"):
                    chart = chart.model_dump()
                df = pd.DataFrame(chart["data"])
                chart_type = chart.get("type", "bar")
                st.caption(f"图表：{chart.get('x_label', '')} vs {chart.get('y_label', '')}")
                if chart_type == "bar":
                    st.bar_chart(df, x="x", y="y")
                elif chart_type == "line":
                    st.line_chart(df, x="x", y="y")
                elif chart_type == "pie":
                    st.dataframe(df)
            # 清除暂停状态
            st.session_state.awaiting_confirmation = False
            st.session_state.pending_sql = None
            # rerun 刷新页面
            # st.rerun()

    with col2:
        if st.button("拒绝"):
            resp = requests.post(f"{API_URL}/resume", json={"thread_id": st.session_state.thread_id, "confirmation": "no"}, timeout=120, headers=HEADERS)
            result = resp.json()

            st.session_state.messages.append(
                {"role": "user", "content": "拒绝执行"}
            )
            st.session_state.messages.append(
                {"role": "assistant", "content": result.get("text", "已拒绝执行")}
            )
            st.session_state.awaiting_confirmation = False
            st.session_state.pending_sql = None
            st.rerun()


# ============ 用户输入框（固定在底部） ============
# 只在不在等确认时才显示输入框，避免用户在 HITL 暂停时输入新问题
# if not st.session_state.awaiting_confirmation:
if not st.session_state.awaiting_confirmation:
    user_input = st.chat_input("问点啥？比如：统计每个客户的总消费金额")
else:
    user_input = None
# user_input = st.chat_input("问点啥？比如：统计每个客户的总消费金额")
if user_input:
    # 1. 先把用户消息加到历史并渲染
    st.session_state.messages.append(
        {"role": "user", "content": user_input}
    )
    with st.chat_message("user"):
        st.write(user_input)
    # 2. 调后端 chat 函数
    with st.chat_message("assistant"):
        with st.spinner("思考中..."):
            resp = requests.post(f"{API_URL}/chat", json={"question": user_input, "thread_id": st.session_state.thread_id,}, timeout=120, headers=HEADERS)
            result = resp.json()

    # 3. 根据 interrupted 标志处理
    if result.get("interrupted"):
        # HITL 暂停：显示 SQL，等用户确认
        st.session_state.pending_sql = result.get("answer", "")
        st.session_state.awaiting_confirmation = True
        # 不立即渲染，rerun 后走上面的 HITL 确认区
        st.rerun()
    else:
        text = result["text"]
        chart = result["chart"]
        # 加历史 + 渲染自然语言
        st.session_state.messages.append({"role": "assistant", "content": text})
        st.write(text)
        # 如果有 chart 块，画图
        if chart:
            if hasattr(chart, "model_dump"):
                chart = chart.model_dump()
            df = pd.DataFrame(chart["data"])
            chart_type = chart.get("type", "bar")
            st.caption(f"图表：{chart.get('x_label', '')} vs {chart.get('y_label', '')}")
            if chart_type == "bar":
                st.bar_chart(df, x="x", y="y")
            elif chart_type == "line":
                st.line_chart(df, x="x", y="y")
            elif chart_type == "pie":
                st.dataframe(df)  # streamlit 内置没有 pie，用表格替代




