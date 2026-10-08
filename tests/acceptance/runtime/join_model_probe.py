"""Actual HTTP provider probe: final answer is derived from received job data."""

import json

from fastapi import FastAPI, Request

app = FastAPI()
settings = {"skill_key": "", "calls": 1}
requests = []


@app.get("/healthz")
async def health():
    return {"status": "ok"}


@app.post("/configure")
async def configure(request: Request):
    settings.update(await request.json())
    requests.clear()
    return {"ok": True}


@app.get("/requests")
async def recorded():
    return {"requests": requests}


@app.post("/v1/chat/completions")
async def completion(request: Request):
    body = await request.json()
    # auth is recorded only by this test-owned probe, never by the application.
    requests.append({"body": body, "authorization": request.headers.get("authorization")})
    messages = body["messages"]
    start = max((index for index, item in enumerate(messages)
        if item["role"] == "user" and not item["content"].startswith("[External tool data")), default=0)
    messages = messages[start:]
    outputs = [
        item["content"]
        for item in messages
        if item["role"] == "user"
        and item["content"].startswith("[External tool data")
        and "BACKGROUND_RESULT" in item["content"]
    ]
    tools = [item for item in messages if item["role"] == "tool"]
    if not tools:
        calls = [
            {
                "id": f"join-call-{index}",
                "type": "function",
                "function": {
                    "name": "execute_skill",
                    "arguments": json.dumps({
                        "skill_key": settings["skill_key"],
                        "completion_mode": "JOIN",
                        "input": {"index": index, "gate": settings["gate"]},
                    }),
                },
            }
            for index in range(settings["calls"])
        ]
        message = {"role": "assistant", "content": "", "tool_calls": calls}
        reason = "tool_calls"
    elif settings["calls"] == 1 and not any(
        item.get("tool_call_id") == "independent-read" for item in tools
    ):
        message = {
            "role": "assistant",
            "content": "Independent work",
            "tool_calls": [
                {
                    "id": "independent-read",
                    "type": "function",
                    "function": {"name": "current_time", "arguments": "{}"},
                }
            ],
        }
        reason = "tool_calls"
    else:
        fallback = "Independent work complete; waiting for the jobs"
        message = {"role": "assistant", "content": "\n".join(outputs) if outputs else fallback}
        reason = "stop"
    return {
        "choices": [{"message": message, "finish_reason": reason}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3},
    }
