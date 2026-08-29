from fastapi import FastAPI, HTTPException, Security, Depends
from pydantic import BaseModel, Field
from typing import Literal, Optional, Any
import os
import uvicorn
from agent_service import chat, resume
from contextlib import asynccontextmanager
import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from fastapi.security import APIKeyHeader

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

import time
import logging
import json

# 简单的结构化日志: 输出成 JSON 一行
logger = logging.getLogger("chatbi")
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}'))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# 限流器: 按 IP 限流, 默认每分钟 5 次
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时创建 checkpointer
    async with aiosqlite.connect("data/checkpoints.db") as conn:
        app.state.checkpointer = AsyncSqliteSaver(conn=conn)
        await app.state.checkpointer.setup()
        yield

app = FastAPI(title="ChatBI Assistant", lifespan=lifespan)

# 这行告诉 FastAPI: 去请求头里找 "X-API-Key" 这个字段
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)
# auto_error=True 表示: 客户端连这个 header 都没带, FastAPI 自动返回 422(参数缺失)

# 正确的 key 从环境变量读, 不写死在代码里(写死了进 git 就泄露了)
API_KEY = os.getenv("CHATBI_API_KEY", "sk-chatbi-dev-123456")

def verify_api_key(api_key :str = Security(api_key_header)):
    # api_key 这个参数, FastAPI 会自动从 header 里取给你
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return api_key

# 挂到 app 上
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)



class ChatRequest(BaseModel):
    question: str = Field(description="用户提问")
    thread_id: str = Field(description="会话id")

class ChatResponse(BaseModel):
    text: Optional[str] = Field(default=None, description="ddl回复文本内容")
    chart: Optional[Any] = Field(default=None, description="图表数据")
    answer: Optional[str] = Field(default=None, description="暂停时的sql")
    interrupted: bool = Field(default=False, description="是否暂停")

class ResumeRequest(BaseModel):
    thread_id: str = Field(description="会话id")
    confirmation: Literal["yes","no"] = Field(description="是否暂停")



@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/chat", response_model=ChatResponse)
@limiter.limit("5/minute")
async def chat_endpoint(request: Request, req: ChatRequest, api_key: str = Depends(verify_api_key)) -> ChatResponse:
    start = time.time()
    result = await chat(req.question, req.thread_id, app.state.checkpointer)
    duration_ms = int((time.time() - start) * 1000)
    logger.info(f"chat called, duration={duration_ms}ms, thread_id={req.thread_id}")
    if not result.get("interrupted"):
        return ChatResponse(text=result["text"], chart=result["chart"], interrupted=result["interrupted"])
    return ChatResponse(answer=result["answer"], interrupted=result["interrupted"])

@app.post("/resume", response_model=ChatResponse)
@limiter.limit("5/minute")
async def resume_endpoint(request: Request, req: ResumeRequest, api_key: str = Depends(verify_api_key)) -> ChatResponse:
     start = time.time()
     result = await resume(req.thread_id, app.state.checkpointer, req.confirmation)
     duration_ms = int((time.time() - start) * 1000)
     logger.info(f"chat called, duration={duration_ms}ms, thread_id={req.thread_id}")
     if not result.get("interrupted"):
         return ChatResponse(text=result["text"], chart=result["chart"], interrupted=result["interrupted"])
     return ChatResponse(answer=result["answer"], interrupted=result["interrupted"])


if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=8090)

