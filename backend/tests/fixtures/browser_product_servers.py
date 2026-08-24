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
                                    "name": "mcp__weather__lookup",
                                    "arguments": '{"query":"fluxion"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
    )


async def evidence(_request: Request) -> JSONResponse:
    return JSONResponse({"model_requests": model_requests, "mcp_calls": mcp_calls})


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
