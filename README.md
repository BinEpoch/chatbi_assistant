# ChatBI 智能问数助手

对话式数据库查询（Text2SQL）Agent：用户用自然语言提问，Agent 自动探索表结构、生成并校验 SQL，经人工确认后执行，返回数据与可视化图表。

历经两个版本迭代：v1 单体应用（InMemorySaver + 同进程工具）→ v2 MCP 微服务架构（工具层独立成服务 + 会话持久化 + 安全防御 + 评测体系）。

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
7. 结果原路返回，Agent 按 Pydantic 结构化规范输出回答 + chart 数据
8. Streamlit 渲染回答与图表

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

打开 http://localhost:8501 开始提问。前端请求需携带 `X-API-Key` 请求头。

### 启动方式二：Docker Compose 一键启动（推荐）

```bash
docker compose up -d --build
```

三个服务（mcp / api / frontend）分别构建镜像并自动组网，容器间通过服务名互相发现，本地无需安装任何 Python 依赖。启动后同样打开 http://localhost:8501 使用。

### 模型配置（环境变量热切换）

LLM 配置已通过环境变量注入（见 `agent_service.py`），切换云端/本地模型零代码改动：

| 变量 | 说明 |
|---|---|
| `LLM_MODEL` | 模型名（默认 `gpt-4o-mini`） |
| `LLM_BASE_URL` | OpenAI 兼容端点（建议环境变量注入，勿写进代码） |
| `LLM_API_KEY` | API Key（建议环境变量注入，勿写进代码） |

示例：切到本地 Ollama 部署的模型，只需设置 `LLM_MODEL=qwen3:0.6b`、`LLM_BASE_URL=http://localhost:11434/v1`。

### 测试问题示例
- 统计每个客户的总消费金额
- 统计每个国家的客户数量
- 2013 年每个月的总销售额

## 评测体系（eval/）

Text2SQL 质量不靠"感觉"，靠可复现的评测协议（参考 Spider/BIRD 的执行准确率思路）：

```bash
python3 eval/run_eval.py                # 全量 30 题
python3 eval/run_eval.py --limit 5      # 冒烟
python3 eval/run_eval.py --golden-only  # 只校验金标准 SQL
```

**评测协议**：LLM 基于库表 schema 生成 SQL → 过与线上相同的两层安全校验 → 金标准 SQL 与生成 SQL 各自执行 → 结果行做多重集合对比（忽略列名与行序，浮点 4 位舍入）。

**最近一次评测结果**（gpt-4o-mini，30 组中文问题，覆盖 easy/medium/hard）：

| 指标 | 结果 |
|---|---|
| SQL 执行准确率 | **86.7%（26/30）** |
| SQL 合法率 | 100%（30/30） |
| 破坏性 SQL 拦截 | **8/8**（DELETE/DROP/UPDATE/INSERT/注释注入/CREATE/ALTER/PRAGMA） |

未通过的 4 题均为真实模型弱点（日期输出格式、销售额口径选择、输出列粒度），明细见 `eval/eval_report.json`。

## 生产化能力

本项目从 demo 级升级到可上线级，补齐了 5 块工程化能力：

### 1. 状态持久化（AsyncSqliteSaver）
- **问题**：v1 的 InMemorySaver 进程重启会话丢失，用户得重头问
- **方案**：换 LangGraph 的 AsyncSqliteSaver，会话状态落 SQLite
- **效果**：进程重启后用户继续之前的对话，无缝衔接

### 2. 人机协同（HITL, Human-in-the-Loop）
- **问题**：LLM 生成的 SQL 直接执行有风险
- **方案**：LangGraph 的 `interrupt` 机制，`execute_sql` 前挂起等待用户确认，`Command(resume)` 恢复执行
- **效果**：用户在 Streamlit 看到 SQL 预览，点"确认执行"或"拒绝"才继续

### 3. SQL 双层安全防御（sqlglot AST + 白名单）
- **问题**：字符串黑名单挡不住注释注入（`SELECT 1; DROP TABLE x;--`）和嵌套破坏语句
- **方案**：第一层 sqlglot 解析 AST，遍历拦截 DELETE/UPDATE/DROP/INSERT/ALTER/CREATE 节点；第二层仅放行 SELECT/WITH 开头的单条语句
- **效果**：8 类破坏性 SQL 回归用例全部拦截（见 `eval/safety_cases.jsonl`）

### 4. API 鉴权 + 限流（APIKeyHeader + slowapi）
- **问题**：服务裸跑在 0.0.0.0:8090，任何人知道 IP 就能调，LLM 额度会被白嫖
- **方案**：`X-API-Key` 请求头校验（key 从环境变量 `CHATBI_API_KEY` 读取，不写死在代码里）；slowapi 按 IP 限流（5 次/分钟，超限 429）
- **效果**：无 key / 错 key 均被 401 拦截（实测），`/health` 探活接口放行

### 5. 结构化日志（JSON logging）
- **方案**：每次 `/chat`、`/resume` 调用输出 JSON 一行日志，含耗时（duration_ms）与 thread_id
- **效果**：可直接接 ELK/Loki 做分析，耗时字段可用于延迟监控

## 模型能力边界实测（Ollama 本地模型热切换）

支持通过环境变量切换任意 OpenAI 兼容端点（云端/本地 Ollama）。实测 `qwen3:0.6b` 本地模型的结论：

> **0.6B 模型能聊天，扛不住 Agent 纪律**——HITL 中断协议不触发、结构化输出（Pydantic response_format）解析失败、工具调用不重试而是蹭会话历史。

结论：Agent 场景对模型的工具调用可靠性与指令遵循要求远高于对话场景，小模型本地化部署需要针对性的微调或更严格的输出约束。

## 技术栈

| 层 | 技术 | 作用 |
|---|---|---|
| 前端 | Streamlit | 对话界面 + 可视化 |
| API 层 | FastAPI + uvicorn | HTTP 接口 + 鉴权 + 限流 |
| Agent | LangChain + LangGraph | create_agent 自动决策 + HITL + 持久化 |
| 工具层 | FastMCP | MCP 协议，工具独立成服务 |
| 数据层 | SQLAlchemy + SQLite | 数据库交互 |
| SQL 安全 | sqlglot | AST 级只读校验 |
| 持久化 | AsyncSqliteSaver | 会话状态落盘，进程重启不丢 |
| 鉴权限流 | APIKeyHeader + slowapi | API Key 鉴权 + 按 IP 限流 |
| 监控 | logging 结构化日志 | JSON 格式日志，可接 ELK/Loki |

## 项目结构

```
chatbi_assistant/
├── backend/
│   ├── db_parser.py        # 数据库工具封装 + sqlglot 双层安全校验
│   ├── mcp_service.py      # MCP Server（4 个工具暴露成 HTTP）
│   ├── tools.py            # MCP Client 包装成 LangChain @tool
│   ├── agent_service.py    # LangGraph create_agent + chat()/resume()（LLM 配置环境变量外置）
│   └── api.py              # FastAPI 路由 + 鉴权 + 限流 + 结构化日志
├── frontend/
│   └── app.py              # Streamlit 前端
├── eval/
│   ├── eval_dataset.jsonl  # 30 组中文问题 + 金标准 SQL
│   ├── safety_cases.jsonl  # 8 类破坏性 SQL 回归用例
│   ├── run_eval.py         # 评测 runner（执行准确率协议）
│   └── eval_report.json    # 最近一次评测报告
└── data/
    └── chinook.db          # SQLite 示例数据库
```

## 已知局限

- **结构化输出靠 prompt + 兜底**：chart 块优先用 pydantic `model_validate_json` 解析，失败时从 ToolMessage 机械构造兜底（取第一列当 x 轴，有局限）
- **Text2SQL 准确率有上限**：当前 86.7%，错误集中在日期格式与口径选择，提升方向是 few-shot 示例注入与 schema linking
- **Streamlit 重跑模型**：每次交互全脚本重跑，demo 级，生产换 React + FastAPI
- **Prometheus 指标未接**：当前只有结构化日志，指标采集/可视化待加
