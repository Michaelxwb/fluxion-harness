"""[S-01][B-141] Runtime→Worker 执行：真实提交、任意 Worker 执行、副作用只发生一次。"""

from __future__ import annotations

from typing import Any, cast

import time
import uuid

import httpx
from muad_agent_runtime.application.task_client import WorkerTaskClient

from .environment import INTERNAL_TOKEN, LiveStack, run_async
from .helpers import load_resolved, submission_context

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def _detail(http: httpx.Client, live_stack: LiveStack, task_id: str) -> dict[str, Any]:
    response = http.get(
        f"{live_stack.worker_url}/internal/tasks/{task_id}", headers=live_stack.service_headers()
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json()["data"])


def _wait_terminal(
    http: httpx.Client, live_stack: LiveStack, task_id: str, timeout_sec: float = 90.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    detail = _detail(http, live_stack, task_id)
    while detail["status"] not in TERMINAL and time.monotonic() < deadline:
        time.sleep(1.0)
        detail = _detail(http, live_stack, task_id)
    return detail


def test_s01_runtime_submits_async_skill_and_worker_completes_once(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    side_effect = live_stack.artifact_root / "probe" / f"side-effects-{uuid.uuid4().hex}.log"
    side_effect.parent.mkdir(parents=True, exist_ok=True)
    context, resolved = submission_context(live_stack)

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

    submitted = run_async(submit)
    task_id = submitted["task_id"]
    assert submitted["status"] == "QUEUED"

    detail = _wait_terminal(http, live_stack, task_id)
    assert detail["status"] == "COMPLETED", detail
    assert detail["result"]["checked"]["side_effect_path"] == str(side_effect)
    assert detail["deadline_at"]
    assert detail["execution_snapshot"]["skills"][0]["artifact_id"] == str(
        resolved["skill"].artifact_id
    )
    assert "api_key" not in str(detail["execution_snapshot"])

    lines = side_effect.read_text(encoding="utf-8").splitlines()
    assert lines == ["executed"], f"副作用必须恰好一次: {lines}"

    events = [event["event_type"] for event in detail["timeline"]]
    assert events[0] == "CREATED" and events[-1] == "COMPLETED"


def test_b141_worker_authority_queued_to_completed_without_redis_hint(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    """PG 是唯一权威：wakeup hint 之外仍由 claim 扫描推进到 COMPLETED。"""
    context, resolved = submission_context(live_stack)

    async def submit() -> dict[str, Any]:
        client = WorkerTaskClient(live_stack.worker_url, service_token=INTERNAL_TOKEN)
        try:
            return await client.submit_task(
                context, skill=resolved["skill"], input_data={"case": "authority"}
            )
        finally:
            await client.aclose()

    task_id = run_async(submit)["task_id"]
    detail = _wait_terminal(http, live_stack, task_id)
    assert detail["status"] == "COMPLETED"
    assert detail["attempt"] >= 1
    assert detail["finished_at"] is not None
