from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from muad_api import ok

router = APIRouter(prefix="/internal/deliveries", tags=["delivery"])


class DeliveryRequest(BaseModel):
    task_id: str
    route: dict[str, Any]
    message: dict[str, Any]
    artifact_ids: list[str] = Field(default_factory=list)


@router.post("")
async def deliver(body: DeliveryRequest, request: Request):
    return ok(request.app.state.catalog, {"accepted": True, "task_id": body.task_id})
