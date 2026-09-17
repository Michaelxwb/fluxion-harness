from fastapi import APIRouter, Request

from muad_api import ok

router = APIRouter(tags=["system"])


@router.get("/healthz")
async def health(request: Request):
    return ok(request.app.state.catalog, {"status": "ok"})
