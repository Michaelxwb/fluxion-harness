"""[B-141] 双 Worker 失约接管与真实 PG 权威。"""

from __future__ import annotations

from typing import Any, cast

import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from muad_contracts import TaskStatus
from sqlalchemy import text

from .environment import LiveStack, run_db
from .helpers import load_resolved, submission_context
from muad_agent_runtime.application.task_client import WorkerTaskClient

from .environment import INTERNAL_TOKEN, run_async


def _detail(http: httpx.Client, live_stack: LiveStack, task_id: str) -> dict[str, Any]:
    response = http.get(
        f"{live_stack.worker_url}/internal/tasks/{task_id}", headers=live_stack.service_headers()
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json()["data"])


def _wait_status(
    http: httpx.Client, live_stack: LiveStack, task_id: str, status: str, timeout_sec: float = 90.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    detail = _detail(http, live_stack, task_id)
    while detail["status"] != status and time.monotonic() < deadline:
        time.sleep(1.0)
        detail = _detail(http, live_stack, task_id)
    return detail


def test_b141_stale_lease_is_taken_over_by_live_worker(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    """失约 Worker 的 RUNNING 任务被运行中的 Worker reclaim 后仍能 COMPLETED。"""
    context, resolved = submission_context(live_stack)

    async def submit() -> dict[str, Any]:
        client = WorkerTaskClient(live_stack.worker_url, service_token=INTERNAL_TOKEN)
        try:
            return await client.submit_task(
                context, skill=resolved["skill"], input_data={"case": "takeover"}
            )
        finally:
            await client.aclose()

    task_id = uuid.UUID(run_async(submit)["task_id"])
    now = datetime.now(UTC)

    async def make_stale(factory: Any) -> None:
        async with factory() as session:
            await session.execute(
                text(
                    "UPDATE task.task_execution SET status = :status, lease_owner = :owner, "
                    "lease_until = :lease_until, attempt = 1, not_before = :not_before "
                    "WHERE id = :id"
                ),
                {
                    "status": str(TaskStatus.RUNNING),
                    "owner": "dead-worker",
                    "lease_until": now - timedelta(seconds=30),
                    "not_before": now - timedelta(minutes=1),
                    "id": task_id,
                },
            )
            await session.commit()

    run_db(make_stale)
    detail = _wait_status(http, live_stack, str(task_id), "COMPLETED")
    assert detail["status"] == "COMPLETED", detail
    assert detail["attempt"] >= 2, "接管必须保留并递增 attempt"

    async def lease_state(factory: Any) -> Any:
        async with factory() as session:
            return (
                await session.execute(
                    text("SELECT lease_owner, lease_until FROM task.task_execution WHERE id = :id"),
                    {"id": task_id},
                )
            ).one()

    lease_owner, lease_until = run_db(lease_state)
    assert lease_owner is None and lease_until is None


def test_b141_both_workers_share_same_authoritative_queue(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    """双 Worker 同进程组：同一 Task 只被一个 Worker 执行一次（PG claim 权威）。"""
    context, resolved = submission_context(live_stack)
    side_effect = live_stack.artifact_root / "probe" / f"takeover-{uuid.uuid4().hex}.log"
    side_effect.parent.mkdir(parents=True, exist_ok=True)

    async def submit() -> dict[str, Any]:
        client = WorkerTaskClient(live_stack.worker_url, service_token=INTERNAL_TOKEN)
        try:
            return await client.submit_task(
                context,
                skill=resolved["skill"],
                input_data={"side_effect_path": str(side_effect)},
            )
        finally:
            await client.aclose()

    task_id = run_async(submit)["task_id"]
    detail = _wait_status(http, live_stack, str(task_id), "COMPLETED")
    assert detail["status"] == "COMPLETED"
    assert side_effect.read_text(encoding="utf-8").splitlines() == ["executed"]
    assert resolved["skill"].checksum.startswith("sha256:")
