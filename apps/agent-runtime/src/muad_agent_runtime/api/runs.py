from __future__ import annotations

import json
import uuid

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from muad_contracts import RunRequest

router = APIRouter(prefix="/v1/runs", tags=["runs"])


@router.post("")
async def create_run(body: RunRequest):
    # SSE 是独立协议，不套 JSON REST Envelope。
    run_id = str(uuid.uuid4())
    conversation_id = str(body.conversation_id or uuid.uuid4())

    async def events():
        created = {
            "run_id": run_id,
            "conversation_id": conversation_id,
            "trace_id": run_id,
        }
        yield f"event: run.created\ndata: {json.dumps(created, ensure_ascii=False)}\n\n"
        completed = {"status": "COMPLETED", "final_text": "runtime skeleton"}
        yield f"event: run.completed\ndata: {json.dumps(completed, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
