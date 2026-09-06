from __future__ import annotations

import argparse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from mcp.server import MCPServer
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

model_requests: list[dict[str, object]] = []
mcp_calls: list[str] = []
mcp = MCPServer("fluxion-browser-product")


@mcp.tool()
def lookup(query: str) -> dict[str, str]:
    mcp_calls.append(query)
    return {"answer": f"MCP found {query}"}


async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def complete(request: Request) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"error": "invalid payload"}, status_code=400)
    model_requests.append(payload)
    messages = payload.get("messages")
    last_role = messages[-1].get("role") if isinstance(messages, list) and messages else None
    if last_role == "tool":
        return JSONResponse(
            {"choices": [{"message": {"role": "assistant", "content": "Browser MCP final answer"}}]}
        )
    # golden-path-closure TASK-026：tool_calls 动态取请求 tools[0]（适配任意 MCP
    # 资源 id 的 runtime tool id 命名）；agent 未挂载任何工具时返回纯文本回复，
    # 使「无工具 Agent 的纯对话」也是可成功路径（空租户 Golden Path）。
    tools = payload.get("tools")
    first_tool_name = None
    if isinstance(tools, list) and tools and isinstance(tools[0], dict):
        function = tools[0].get("function")
        if isinstance(function, dict):
            first_tool_name = function.get("name")
    if first_tool_name:
        return JSONResponse(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "browser-call-1",
                                    "type": "function",
                                    "function": {
                                        "name": first_tool_name,
                                        "arguments": '{"query":"fluxion"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )
    return JSONResponse(
        {"choices": [{"message": {"role": "assistant", "content": "Fluxion golden path reply"}}]}
    )


async def evidence(_request: Request) -> JSONResponse:
    return JSONResponse({"model_requests": model_requests, "mcp_calls": mcp_calls})


async def models(_request: Request) -> JSONResponse:
    # golden-path-closure TASK-010：OpenAI-compatible 模型发现端点（Test Connection
    # / Discover Models 真实探测目标；F-S-03 E2E 与空租户 Golden Path 复用）。
    return JSONResponse(
        {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}
    )


def create_app() -> Starlette:
    mcp_app = mcp.streamable_http_app(json_response=True, stateless_http=True)

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    return Starlette(
        routes=[
            Route("/healthz", healthz),
            Route("/v1/chat/completions", complete, methods=["POST"]),
            Route("/v1/models", models, methods=["GET"]),
            Route("/evidence", evidence),
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9878)
    args = parser.parse_args()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
