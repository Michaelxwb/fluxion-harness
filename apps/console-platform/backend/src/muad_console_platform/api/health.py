from typing import Any

from fastapi import APIRouter, Request
from muad_api import ApiResponse, ok

router = APIRouter(tags=["system"])


@router.get("/healthz")
async def health(request: Request) -> ApiResponse[Any]:
    return ok(request.app.state.message_catalog, {"status": "ok"})
