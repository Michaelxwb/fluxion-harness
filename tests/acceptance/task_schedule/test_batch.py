"""[S-04][B-143] BATCH 幂等 fan-out、并发上限与原子 fan-in。"""

from __future__ import annotations

from typing import Any, cast

import hashlib
import time
import uuid
import zipfile
from dataclasses import replace

import httpx
from muad_contracts import DeliveryMode, SkillExecutionMode
from sqlalchemy import text

from .environment import INTERNAL_TOKEN, LiveStack, run_async, run_db
from .helpers import load_resolved, submission_context
from muad_agent_runtime.application.task_client import WorkerTaskClient

BATCH_SCRIPT = (
    "import json, sys\n"
    "payload = json.loads(sys.stdin.read() or '{}')\n"
    "if 'customers' in payload:\n"
    "    print(json.dumps({'batch': {'items': [{'customer': c} for c in payload['customers']],"
    " 'max_concurrency': 2, 'aggregate_mode': 'ALL'}}))\n"
    "else:\n"
    "    print(json.dumps({'checked': payload.get('customer')}))\n"
)


def _submit_batch(live_stack: LiveStack) -> str:
    context, resolved = submission_context(live_stack)
    batch_skill = resolved["skill"].model_copy(
        update={
            "artifact_id": live_stack.batch_artifact_id,
            "storage_key": live_stack.batch_storage_key,
            "checksum": live_stack.batch_checksum,
            "execution_mode": SkillExecutionMode.ASYNC,
            "version": "batch-1.0.0",
        }
    )
    context = replace(context, skills=(batch_skill,))

    async def submit() -> dict[str, Any]:
        client = WorkerTaskClient(live_stack.worker_url, service_token=INTERNAL_TOKEN)
        try:
            return await client.submit_task(
                context,
                skill=batch_skill,
                input_data={"customers": ["A", "B", "C", "D"]},
                intent_key="e2e_batch",
            )
        finally:
            await client.aclose()

    return str(run_async(submit)["task_id"])


def _rows(live_stack: LiveStack, task_id: str) -> tuple[Any, ...]:
    async def query(factory: Any) -> Any:
        async with factory() as session:
            parent = (
                await session.execute(
                    text(
                        "SELECT status, task_type, result_json, delivery_mode, error_code, "
                        "left(error_message, 300) FROM task.task_execution WHERE id = :id"
                    ),
                    {"id": uuid.UUID(task_id)},
                )
            ).one()
            children = list(
                (
                    await session.execute(
                        text(
                            "SELECT id, status, item_key, delivery_mode, idempotency_key, root_id "
                            "FROM task.task_execution WHERE parent_id = :id ORDER BY item_key"
                        ),
                        {"id": uuid.UUID(task_id)},
                    )
                ).all()
            )
            return parent, children

    return cast(tuple[Any, ...], run_db(query))


def test_s04_batch_fanout_fanin_aggregates_parent(live_stack: LiveStack, http: httpx.Client) -> None:
    task_id = _submit_batch(live_stack)

    def parent_terminal() -> bool:
        parent, _ = _rows(live_stack, task_id)
        return parent[0] in {"COMPLETED", "FAILED", "CANCELLED"}

    deadline = time.monotonic() + 120
    while not parent_terminal() and time.monotonic() < deadline:
        time.sleep(1.0)
    parent, children = _rows(live_stack, task_id)
    assert parent[0] == "COMPLETED", parent
    assert parent[1] == "BATCH"
    assert parent[3] == str(DeliveryMode.NONE)
    assert parent[2]["total"] == 4 and parent[2]["succeeded"] == 4, parent[2]

    assert len(children) == 4
    for child in children:
        assert child[1] == "COMPLETED"
        assert child[3] == str(DeliveryMode.NONE)
        assert child[4] == f"parent:{task_id}:{child[2]}"
        assert str(child[5]) == task_id

    async def count_events(factory: Any) -> Any:
        async with factory() as session:
            return (
                await session.execute(
                    text(
                        "SELECT count(*) FROM task.task_event WHERE task_id = :id AND event_type = 'FAN_IN'"
                    ),
                    {"id": uuid.UUID(task_id)},
                )
            ).scalar_one()

    assert run_db(count_events) == 1
