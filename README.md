# ChatBI 助手

对话式数据库查询助手，用户用自然语言提问，Agent 自动决策调用 SQL 工具查询数据库，并生成可视化图表。

## 项目简介

基于 LangGraph `create_agent` 实现的对话式数据库查询助手。核心能力：
- **自然语言查数**：用户问"统计每个客户的总消费金额"，Agent 自动生成并执行 SQL
- **工具层解耦**：数据库工具用 FastMCP 独立成 HTTP 服务，可被多 Agent 复用
- **可视化**：LLM 按 prompt 规则追加结构化 chart 块，前端 Streamlit 解析后画图

## 架构

```
用户 → Streamlit (前端) → FastAPI (API层) → Agent (LangGraph) → MCP (工具层) → DBParser → SQLite
                                                       ↑
                工具: list_tables / describe_table / get_table_sample / execute_sql
```

**数据流**：
1. 用户在 Streamlit 输入问题
2. Streamlit POST 请求 FastAPI `/chat` 端点
3. FastAPI 调 `agent_service.py` 的 `chat()` 函数
4. Agent（LangGraph create_agent）自动决策调工具
5. Agent 通过 MCP Client 调 MCP Server 的工具
6. MCP Server 调 DBParser 查 SQLite
7. 结果原路返回，Agent 整理成中文回答 + chart 块
8. Streamlit 解析 chart 块画图

## 快速开始

### 环境要求
- Python 3.10+
- 依赖：`pip install langchain langgraph langchain-openai fastmcp fastapi uvicorn sqlalchemy pandas streamlit`

### 启动（三个终端，按顺序）

```bash
# 终端1：启动 MCP Server（工具层，端口 8000）
cd backend && python mcp_service.py

# 终端2：启动 FastAPI（API 层，端口 8090）
cd backend && python api.py

# 终端3：启动 Streamlit（前端，端口 8501）
cd frontend && streamlit run app.py
```

打开 http://localhost:8501 开始提问。

### 测试问题示例
- 统计每个客户的总消费金额
- 统计每个国家的客户数量
- 2013 年每个月的总销售额

## 技术栈

| 层 | 技术 | 作用 |
|---|---|---|
| 前端 | Streamlit | 对话界面 + 可视化 |
| API 层 | FastAPI + uvicorn | HTTP 接口 + 鉴权 + 限流 |
| Agent | LangChain + LangGraph | create_agent 自动决策 + HITL + 持久化 |
| 工具层 | FastMCP | MCP 协议，工具独立成服务 |
| 数据层 | SQLAlchemy + SQLite | 数据库交互 |
| 持久化 | AsyncSqliteSaver | 会话状态落盘，进程重启不丢 |
| 鉴权限流 | APIKeyHeader + slowapi | API Key 鉴权 + 按 IP 限流 |
| 监控 | logging 结构化日志 | JSON 格式日志，可接 ELK/Loki |

## 项目结构

```
chatbi_assistant/
├── backend/
│   ├── db_parser.py        # 数据库工具封装（SQLAlchemy）
│   ├── mcp_service.py      # MCP Server（4 个工具暴露成 HTTP）
│   ├── tools.py            # MCP Client 包装成 LangChain @tool
│   ├── agent_service.py    # LangGraph create_agent + chat()/resume()
│   └── api.py              # FastAPI 路由
├── frontend/
│   └── app.py              # Streamlit 前端
└── data/
    └── chinook.db          # SQLite 示例数据库
```

## 生产化能力（第12周 ChatBI 进阶）

本项目从 demo 级升级到可上线级，补齐了 4 块工程化能力：

### 1. 状态持久化（AsyncSqliteSaver）
- **问题**：原 InMemorySaver 进程重启会话丢失，用户得重头问
- **方案**：换 LangGraph 的 AsyncSqliteSaver，会话状态落 SQLite
- **效果**：进程重启后用户继续之前的对话，无缝衔接

### 2. 人机协同（HITL, Human-in-the-Loop）
- **问题**：LLM 生成的 SQL 直接执行，有 DROP/DELETE 等危险操作时无拦截
- **方案**：LangGraph 的 `interrupt` 机制，execute_sql 前暂停，等用户确认
- **效果**：用户在 Streamlit 看到 SQL 预览，点"确认执行"或"拒绝"才继续

### 3. 鉴权（API Key + FastAPI Dependency）
- **问题**：服务裸跑在 0.0.0.0:8090，任何人知道 IP 就能调，LLM 额度会被白嫖
- **方案**：FastAPI 的 `APIKeyHeader` + `Depends`，请求头带 `X-API-Key`，后端校验不对抛 401
- **效果**：无 key 被 403 拦截，错 key 被 401 拒绝，对 key 才放行；`/health` 探活接口放行

### 4. 限流 + 结构化日志（slowapi + JSON logging）
- **问题**：即便有鉴权，拿到 key 的人也可能疯狂调用烧额度
- **方案**：slowapi 按 IP 限流（每分钟 5 次），超限返回 429；logging 输出 JSON 结构化日志
- **效果**：6 次调用第 6 次被 429 拦截，1 分钟后自动恢复；日志含耗时/thread_id，可接 ELK/Loki

## 已知局限

- **结构化输出靠 prompt + 兜底**：chart 块优先用 pydantic `model_validate_json` 解析，失败时从 ToolMessage 机械构造兜底（取第一列当 x 轴，有局限）
- **Streamlit 重跑模型**：每次交互全脚本重跑，demo 级，生产换 React + FastAPI
- **Prometheus 指标未接**：当前只有结构化日志，指标采集/可视化待加
