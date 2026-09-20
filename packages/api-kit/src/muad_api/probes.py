from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .response import ok

ReadinessCheck = Callable[[], bool | Awaitable[bool]]
ReadinessDetail = Callable[[], Mapping[str, Any]]


def database_readiness(engine_factory: Callable[[], AsyncEngine]) -> ReadinessCheck:
    """就绪检查：数据库可达（真实连接 + SELECT 1，不做缓存）。

    引擎用惰性工厂传入，避免在模块导入期就要求 `DATABASE_URL`。
    """

    async def check() -> bool:
        try:
            engine = engine_factory()
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            return False
        return True

    return check


async def _failed_checks(checks: Mapping[str, ReadinessCheck]) -> list[str]:
    failed: list[str] = []
    for name, check in checks.items():
        try:
            result = check()
            if inspect.isawaitable(result):
                result = await result
            if not result:
                failed.append(name)
        except Exception:
            failed.append(name)
    return failed


def _probe_response(request: Request, status_code: int, data: dict[str, Any]) -> JSONResponse:
    """已安装 api-kit 封套时按统一 Envelope 返回，否则回落到裸 JSON（供库级单测）。"""
    catalog = getattr(request.app.state, "message_catalog", None)
    if catalog is None:
        return JSONResponse(status_code=status_code, content=data)
    return JSONResponse(status_code=status_code, content=ok(catalog, data).model_dump(mode="json"))


def install_health_probes(
    app: FastAPI,
    readiness_checks: Mapping[str, ReadinessCheck] | Sequence[tuple[str, ReadinessCheck]] = (),
    *,
    detail: ReadinessDetail | None = None,
) -> None:
    """注册 `/healthz`（进程存活）与 `/readyz`（依赖就绪）。

    - `readiness_checks`：名称 → 检查函数；任一为假或抛异常 → `/readyz` 503 并在 `failed` 列出名称。
    - `detail`：可选，附加到 `/readyz` 响应体的诊断信息（如适配器状态、缓存 revision）。
    """
    checks = dict(readiness_checks)

    @app.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        return _probe_response(request, 200, {"status": "ok"})

    @app.get("/readyz")
    async def readyz(request: Request) -> JSONResponse:
        failed = await _failed_checks(checks)
        data: dict[str, Any] = {"status": "unavailable" if failed else "ready"}
        if failed:
            data["failed"] = failed
        if detail is not None:
            data.update(detail())
        return _probe_response(request, 503 if failed else 200, data)
