from fastapi import APIRouter, Request

from muad_api import AppError, ok
from muad_api.error_codes import ErrorCode

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

_demo = {
    "id": "00000000-0000-0000-0000-000000000001",
    "name": "MSS 服务助手",
    "key": "mss-assistant",
    "revision": 1,
    "enabled": True,
}


@router.get("")
async def list_agents(request: Request):
    return ok(
        request.app.state.catalog,
        {"items": [_demo], "page": 1, "page_size": 20, "total": 1},
    )


@router.get("/{agent_id}")
async def get_agent(agent_id: str, request: Request):
    if agent_id != _demo["id"]:
        raise AppError(ErrorCode.AGENT_NOT_FOUND)
    return ok(request.app.state.catalog, _demo)
