"""本地渠道探针：真实 HTTP 进程，记录 Gateway 投递并支持故障注入。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

_state: dict[str, Any] = {"deliveries": [], "fail_next": 0}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/probe/deliveries")
async def record_delivery(request: Request) -> Any:
    payload = await request.json()
    if _state["fail_next"] > 0:
        _state["fail_next"] -= 1
        return JSONResponse(
            {"accepted": False, "error": "probe injected failure"}, status_code=500
        )
    _state["deliveries"].append(payload)
    return {"accepted": True}


@app.get("/probe/deliveries")
async def list_deliveries() -> dict[str, Any]:
    return {"deliveries": list(_state["deliveries"])}


@app.post("/probe/fail-next")
async def fail_next(request: Request) -> dict[str, Any]:
    payload = await request.json()
    _state["fail_next"] = int(payload.get("count", 1))
    return {"fail_next": _state["fail_next"]}


@app.post("/probe/reset")
async def reset() -> dict[str, bool]:
    _state["deliveries"] = []
    _state["fail_next"] = 0
    return {"reset": True}
