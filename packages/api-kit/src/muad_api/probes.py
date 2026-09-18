from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence

from fastapi import FastAPI
from fastapi.responses import JSONResponse

ReadinessCheck = Callable[[], bool | Awaitable[bool]]


def install_health_probes(
    app: FastAPI,
    readiness_checks: Mapping[str, ReadinessCheck] | Sequence[tuple[str, ReadinessCheck]],
) -> None:
    checks = dict(readiness_checks)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse(status_code=200, content={"status": "ok"})

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
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
        if failed:
            return JSONResponse(status_code=503, content={"status": "unavailable", "failed": failed})
        return JSONResponse(status_code=200, content={"status": "ready"})
