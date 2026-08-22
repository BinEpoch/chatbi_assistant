1.列出本周要写的所有文件，每个文件一句话说作用

backend/
  db_parser.py     模型的工具，封装成类，四个方法对应4个工具，实际执行工具逻辑的地方
  mcp_service.py   工具封装到fastmcp，暴露给http，端口号8000
  tools.py         mcp的四个工具包装成langchain识别的工具，怎么连接上我还不知道
  agent_service.py    定义langchain agent，提供chat()和resum()两个函数给api调用，agent内部自己觉得调用tool的哪个工具
  api.py              用来接受前端入如的信息，集成在fastapi，调用 agent_service方法  
frontend/
  app.py     前端代码
data/
  chinook.db  数据库

2 数据流：用户问"统计每个客户的总消费金额"，从 Streamlit 到 chinook.db 完整链路写一遍。要回答：
Streamlit 调 FastAPI 哪个端点？
FastAPI 调 agent_service 哪个函数？
Agent 调哪个工具？工具是 LangChain @tool 还是 MCP？
MCP Server 收到请求后调 DBParser 哪个方法？
结果怎么原路返回？

1.用户在Streamlit上输入"统计每个客户的总消费金额"，点发送
2.Streamlit(app.py) 发HTTP POST请求到FastAPI(api.py)的 /chat 端点
3.FastAPI(api.py)的chat函数接到请求，调 agent_service.py 的chat(question, thread_id)
4.aagent_service.py 的chat() 把问题传给 Langchain Agent的（create_agent创建的）
5.Agent(LangGraph自动ReAct循环)决定先看看有那些表，调tool.py的list_tables @函数
6.tool.py 的list_tables()函数体：用MCP Client连mcp_server.py, call_tool("get_table_name",{})
7.mcp_server.py 收到MCP请求，调db_parser.py 的 get_table_name()方法
8.db_parser连接chinook.db 查sqlite_master表，返回表名列表
9.结果原路返回：db_parser->mcp_server->tools.py->Agent
10.Agent拿到表名，自己决定下一步调tool.py的py的describe_table看字段（同上链路）
11.Agent继续决策：生成sql->调tool.py的execute_sql->同上链路拿到结果
12.Agent整合sql结果生成自然语言回答
13.agent_service.py的chat()返回给api.py
14.api.py 返回HTTP响应给Streamlit
15.Streamlit展示回答



3.Day2 你要把 MCP 工具包成 LangChain @tool，这是 Week9+Week10 整合的核心。你先想想：
Week9 的 @tool 是直接调 Python 函数（同进程）
Week10 的工具在独立进程（FastMCP Server），LangChain 怎么去调？
提示：参考 Week7 Day1 的 MCP Client（fastmcp.Client + call_tool），或者 LangChain 的 langchain-mcp-adapters 库

外壳是Langchain@tool，内核用MCP Client连接MCP service


维度                      chatbi_assistant                         text2sql_agent                  smart_data_assistant
Agent 框架                 langchain                               openai-agent                     langchain
工具层协议                    mcp                                        mcp                          无       
工具是否独立进程              是                                              是                        否
前端                         有                                             无                        有
多轮记忆                   checkpointer                              可以应session_id,实际没用        checkpointer         
HITL                不知道推测insterrupt+Commend                            无                      insterrupt+Commend    
  



