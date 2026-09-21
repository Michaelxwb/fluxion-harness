"""E2E 用真实 OpenAI 兼容探测端点（真实 HTTP：健康响应 + 可选脚本化工具调用）。

行为由环境变量控制（请求时读取，便于同一进程内切换）：
- OPENAI_PROBE_DELAY_MS：响应前延迟毫秒数
- OPENAI_PROBE_FAIL=1：返回 500
- OPENAI_PROBE_REQUIRE_AUTH：非空时校验 Authorization: Bearer
- OPENAI_PROBE_TOOL_NAME：非空时首轮返回该工具调用，收到 tool 结果后返回最终文本
- OPENAI_PROBE_FINAL_TEXT：最终文本（默认 pong）
"""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

DEFAULT_FINAL_TEXT = "pong"


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models() -> dict[str, list[dict[str, str]]]:
    return {"data": [{"id": "gpt-4o-mini", "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    delay_ms = int(os.environ.get("OPENAI_PROBE_DELAY_MS", "0"))
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000)
    required = os.environ.get("OPENAI_PROBE_REQUIRE_AUTH", "")
    if required and request.headers.get("authorization") != f"Bearer {required}":
        return JSONResponse(
            {"error": {"message": "unauthorized", "type": "invalid_request_error"}},
            status_code=401,
        )
    if os.environ.get("OPENAI_PROBE_FAIL") == "1":
        return JSONResponse({"error": {"message": "probe failure"}}, status_code=500)

    body = await request.json()
    messages = body.get("messages") or []
    tool_name = os.environ.get("OPENAI_PROBE_TOOL_NAME", "")
    if tool_name:
        has_tool_result = any(
            isinstance(message, dict) and message.get("role") == "tool" for message in messages
        )
        if not has_tool_result:
            return {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-probe-1",
                                    "type": "function",
                                    "function": {
                                        "name": tool_name,
                                        "arguments": '{"query": "ping"}',
                                    },
                                }
                            ],
                        },
                    }
                ]
            }
    final_text = os.environ.get("OPENAI_PROBE_FINAL_TEXT", DEFAULT_FINAL_TEXT)
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": final_text},
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
