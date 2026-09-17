import uuid

from fastapi import APIRouter, Request

from muad_api import ok
from muad_contracts import CreateTaskRequest

router = APIRouter(prefix="/internal/tasks", tags=["tasks"])


@router.post("")
async def create_task(body: CreateTaskRequest, request: Request):
    return ok(request.app.state.catalog, {"task_id": str(uuid.uuid4()), "status": "QUEUED"})
