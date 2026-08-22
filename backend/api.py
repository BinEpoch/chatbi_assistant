from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import Literal
import asyncio
import uvicorn
from agent_service import chat, resume


app = FastAPI(title="ChatBI Assistant")

class ChatRequest(BaseModel):
    question: str = Field(description="用户提问")
    thread_id: str = Field(description="会话id")

class ChatResponse(BaseModel):
    answer: str = Field(description="ddl回复")
    interrupted: bool = Field(description="是否暂停")

class ResumeRequest(BaseModel):
    thread_id: str = Field(description="会话id")
    confirmation: Literal["yes","no"] = Field(description="是否暂停")



@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest) -> ChatResponse:
    result = await chat(req.question, req.thread_id)
    return ChatResponse(answer=result["answer"], interrupted=result["interrupted"])

@app.post("/resume", response_model=ChatResponse)
async def resume_endpoint(req: ResumeRequest) -> ChatResponse:
     result = await resume(req.thread_id, req.confirmation)
     return ChatResponse(answer=result["answer"],interrupted=result["interrupted"])

if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=8090)

