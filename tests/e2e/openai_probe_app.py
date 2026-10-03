"""E2E 用真实 OpenAI 兼容探测端点（真实 HTTP：健康响应 + 可选脚本化工具调用）。

行为由环境变量控制（请求时读取，便于同一进程内切换）：
- OPENAI_PROBE_DELAY_MS：响应前延迟毫秒数
- OPENAI_PROBE_FAIL=1：返回 500
- OPENAI_PROBE_REQUIRE_AUTH：非空时校验 Authorization: Bearer
- OPENAI_PROBE_TOOL_NAME：非空时首轮返回该工具调用，收到 tool 结果后返回最终文本
- 脚本还支持 **`tools`（工具序列）**：`POST /script {"tools":[{"name":…,"arguments":…}, …],
  "final_text":…}` —— 第 n 轮返回第 n 个工具调用，序列走完后才给 `final_text`。
  单工具脚本表达不了「先写、再交付」这类**多步**链路，而那是本需求的核心场景。
- OPENAI_PROBE_TOOL_ARGUMENTS：上述工具调用的 arguments（JSON 字符串，默认 `{"query": "ping"}`）
- OPENAI_PROBE_FINAL_TEXT：最终文本（默认 pong）

另提供 `GET /requests`：返回本进程收到的全部 chat/completions 请求体（最近 50 条），
供验收用例断言"模型实际收到了什么"（例如工具结果内容是否带 `saved: true`）。
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

DEFAULT_FINAL_TEXT = "pong"
#: 入站附件的文本引用形如 `notes.txt（text/plain，12 字节，附件 ID 4f0c…）`
ARTIFACT_ID_RE = re.compile(r"附件 ID ([0-9a-fA-F-]{36})")
ARTIFACT_ID_PLACEHOLDER = "$last_artifact_id"
DEFAULT_TOOL_ARGUMENTS = '{"query": "ping"}'
RECORDED_LIMIT = 50
_received: list[dict[str, Any]] = []
#: 运行期脚本（`POST /script`）；空 = 用 env 默认
_script: dict[str, Any] = {}


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "") for part in content if isinstance(part, dict)
        )
    return ""


def _last_user_index(messages: list[Any]) -> int:
    """最后一条 user 消息的下标（没有则 0，即整份都算"本轮"）。"""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, dict) and message.get("role") == "user":
            return index
    return 0


def _last_artifact_id(messages: list[Any]) -> str:
    """从模型实际收到的上下文里取**最近一条**附件引用里的 ID（与真实模型看到的是同一份文本）。

    倒着找是必须的：历史轮次的附件也带 ID，正着找会拿到上一轮那个，工具于是读错文件。
    """
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        match = ARTIFACT_ID_RE.search(_message_text(message))
        if match:
            return match.group(1)
    return ""


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/requests")
async def received_requests() -> dict[str, list[dict[str, Any]]]:
    return {"requests": list(_received)}


@app.get("/script")
async def get_script() -> dict[str, Any]:
    return {"script": dict(_script)}


@app.post("/script")
async def set_script(request: Request) -> dict[str, Any]:
    """设定后续请求的模型行为；传 `{}` 即清除（回到 env 默认）。"""
    payload = await request.json()
    _script.clear()
    if isinstance(payload, dict):
        allowed = ("tool_name", "tool_arguments", "final_text", "tools")
        _script.update({key: payload[key] for key in allowed if key in payload})
    return {"script": dict(_script)}


def _substitute(arguments: str, messages: list[Any]) -> str:
    """`$last_artifact_id` 占位 → 从**模型实际看到的**上下文里倒着找最近一条附件 ID。"""
    if ARTIFACT_ID_PLACEHOLDER in arguments:
        return arguments.replace(ARTIFACT_ID_PLACEHOLDER, _last_artifact_id(messages))
    return arguments


def _tool_call(name: str, arguments: str, *, index: int) -> dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"call-probe-{index}",
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                },
            }
        ]
    }


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
    _received.append(body)
    del _received[:-RECORDED_LIMIT]
    messages = body.get("messages") or []
    tool_name = str(_script.get("tool_name") or os.environ.get("OPENAI_PROBE_TOOL_NAME", ""))
    tool_arguments = str(
        _script.get("tool_arguments") or os.environ.get("OPENAI_PROBE_TOOL_ARGUMENTS", DEFAULT_TOOL_ARGUMENTS)
    )
    # 「**本轮**已经拿到几个工具结果」——只看最后一条 user 消息之后的那些消息。
    # 不能看整份 messages：历史轮次里的 tool 消息会让探针以为本轮已经调过工具，于是
    # 直接给 final_text，脚本里的工具永远不被调用（实测：同一会话先跑过别的工具回合时必现）。
    completed = sum(
        1
        for message in messages[_last_user_index(messages) :]
        if isinstance(message, dict) and message.get("role") == "tool"
    )
    steps = _script.get("tools")
    if isinstance(steps, list) and steps:
        # 工具序列：第 `completed` 步还没走完就再调一个，全部走完才落到 final_text。
        # 这一步**不看 `tool_name`**：序列形态本身就不设 `tool_name`（多步链路用它表达），
        # 挂在 `if tool_name` 下面会让它永远不生效——实测踩过，表现为"脚本设了但一个工具都没调"。
        if completed < len(steps):
            step = steps[completed] if isinstance(steps[completed], dict) else {}
            return _tool_call(
                str(step.get("name") or ""),
                _substitute(str(step.get("arguments") or "{}"), messages),
                index=completed + 1,
            )
    elif tool_name and completed == 0:
        return _tool_call(tool_name, _substitute(tool_arguments, messages), index=1)
    final_text = str(
        _script.get("final_text") or os.environ.get("OPENAI_PROBE_FINAL_TEXT", DEFAULT_FINAL_TEXT)
    )
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": final_text},
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
