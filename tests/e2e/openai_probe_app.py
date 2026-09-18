"""E2E 用真实 OpenAI 兼容探测端点（仅返回健康响应）。"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI()


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models() -> dict[str, list[dict[str, str]]]:
    return {"data": [{"id": "gpt-4o-mini", "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat_completions() -> dict[str, object]:
    return {"choices": [{"message": {"role": "assistant", "content": "pong"}}]}
