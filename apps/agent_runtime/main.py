from fastapi import APIRouter
from pydantic import BaseModel

from framework.web.app import create_app
from framework.web.response import ApiResponse, ok

app = create_app(title="Intelligent Service Framework - Agent Runtime", service_name="agent-runtime")
router = APIRouter(prefix="/internal/v1")


class ChatRequest(BaseModel):
    agent_id: str
    conversation_id: str | None = None
    content: str


@router.get("/health", response_model=ApiResponse[dict[str, str]])
async def health() -> ApiResponse[dict[str, str]]:
    return ok({"status": "ok", "service": "agent-runtime"})


@router.post("/chat", response_model=ApiResponse[dict[str, str]])
async def chat(request: ChatRequest) -> ApiResponse[dict[str, str]]:
    # TODO: resolve AgentDefinition + Conversation + UserMemory + capabilities,
    # then invoke the shared LangGraph AgentExecutor.
    return ok({"agent_id": request.agent_id, "content": "agent-runtime scaffold"})


app.include_router(router)
