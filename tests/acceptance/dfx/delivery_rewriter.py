"""真实 HTTP 响应改写代理：Worker ↔ 真实 IM Gateway 之间的**故障注入**一跳（E-06 用）。

用途：
- `REWRITE_FORCE_NOT_DELIVERED=1`：把真实 Gateway 的 2xx 响应改写成「只占位、未确认送达」
  （`delivered=false`）。生产 Gateway 当前**没有**任何路径会返回 2xx + `delivered=false`
  （它的 in-flight 重放超时后回 5xx），故这一支的响应形状只能由注入器造出。
- `REWRITE_DELAY_SEC`：在把响应交回 Worker 之前延迟有界时间。

它**不替换** Gateway / Redis / 渠道探针：请求原样转发给真实 Gateway（真实 Redis 去重、
真实渠道探针收件），只在**回程**延迟/改写响应。延迟刻意放在回程——此刻真实渠道已经收到
请求（或已失败），而 Worker 仍在等待响应，于是「投递预留已提交」成为可观测的库内事实。

**请求头原样转发**（2026-10-06）：`/internal/deliveries` 现在要求 `X-Internal-Service`，
代理吞掉它就等于把每一条 E-06 路径都变成 403 —— 而 403 看起来像"投递失败"，会把故障注入的
结论整个带偏。
"""

from __future__ import annotations

import asyncio
import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

DELIVERIES_PATH = "/internal/deliveries"
#: 透传的请求头：调用方身份与链路标识。**不放行**的内容类型由 httpx 自己按 json 重设。
FORWARDED_HEADERS = ("x-internal-service", "x-tenant-id", "x-trace-id", "x-request-id", "x-caller-service")
ENV_UPSTREAM = "REWRITE_UPSTREAM_GATEWAY_URL"
ENV_DELAY_SEC = "REWRITE_DELAY_SEC"
ENV_FORCE_NOT_DELIVERED = "REWRITE_FORCE_NOT_DELIVERED"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _upstream_gateway_url() -> str:
    value = os.environ.get(ENV_UPSTREAM)
    if not value:
        raise RuntimeError(f"{ENV_UPSTREAM} 未配置：改写代理必须转发到真实 Gateway")
    return value.rstrip("/")


def _delay_sec() -> float:
    return float(os.environ.get(ENV_DELAY_SEC, "0"))


def _force_not_delivered() -> bool:
    return os.environ.get(ENV_FORCE_NOT_DELIVERED) == "1"


def _forwarded_headers(request: Request) -> dict[str, str]:
    return {
        name: value
        for name, value in request.headers.items()
        if name.lower() in FORWARDED_HEADERS
    }


def _placeholder_envelope() -> dict[str, object]:
    """与真实 Gateway 同形的封套，`data.delivered=False` 表达「已受理但未送达」。"""
    return {
        "code": "0",
        "msg": "ok",
        "data": {"accepted": True, "duplicate": False, "delivered": False, "deduplicated": False},
        "trace_id": "rewriter",
        "request_id": "rewriter",
        "timestamp": "",
    }


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post(DELIVERIES_PATH)
async def deliver(request: Request) -> Response:
    payload = await request.json()
    async with httpx.AsyncClient(timeout=30.0) as client:
        upstream = await client.post(
            f"{_upstream_gateway_url()}{DELIVERIES_PATH}",
            json=payload,
            headers=_forwarded_headers(request),
        )
    if _delay_sec() > 0:
        await asyncio.sleep(_delay_sec())
    if _force_not_delivered():
        return JSONResponse(_placeholder_envelope(), status_code=200)
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type="application/json",
    )
