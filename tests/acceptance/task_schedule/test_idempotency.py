"""[B-141][B-201][E-07] 真实 Worker HTTP 的提交幂等与冲突。"""

from __future__ import annotations

from typing import Any, cast

import json
import uuid

import httpx
from muad_agent_runtime.application.task_client import WorkerTaskClient

from .environment import INTERNAL_TOKEN, LiveStack, run_async
from .helpers import load_resolved, submission_context


def _headers(live_stack: LiveStack, idempotency_key: str) -> dict[str, str]:
    return {**live_stack.service_headers(), "Idempotency-Key": idempotency_key}


def _create_task_body(live_stack: LiveStack, *, intent_key: str = "e2e_policy_check") -> dict[str, Any]:
    resolved = load_resolved(live_stack)
    snapshot, snapshot_hash = _snapshot(live_stack)
    return {
        "tenant_id": live_stack.tenant_id,
        "agent_id": str(live_stack.agent_id),
        "actor_user_id": str(live_stack.platform_user_id),
        "intent_key": intent_key,
        "skill_id": str(resolved["skill"].skill_id),
        "skill_artifact_id": str(resolved["skill"].artifact_id),
        "input": {},
        "execution_snapshot": snapshot,
        "snapshot_hash": snapshot_hash,
        "idempotency_key": "placeholder",
        "delivery_mode": "NONE",
    }


def _snapshot(live_stack: LiveStack) -> tuple[dict[str, Any], str]:
    context, resolved = submission_context(live_stack)
    from muad_agent_runtime.application.task_client import build_task_snapshot

    return build_task_snapshot(context, resolved["skill"])


def test_b141_same_key_same_fingerprint_replays_first_result(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    key = f"e2e-idem-{uuid.uuid4()}"
    body = _create_task_body(live_stack)
    body["idempotency_key"] = key

    first = http.post(
        f"{live_stack.worker_url}/internal/tasks", json=body, headers=_headers(live_stack, key)
    )
    assert first.status_code == 200, first.text
    first_task = first.json()["data"]["task_id"]

    replay = http.post(
        f"{live_stack.worker_url}/internal/tasks", json=body, headers=_headers(live_stack, key)
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["task_id"] == first_task


def test_b141_same_key_different_fingerprint_conflicts(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    key = f"e2e-idem-conflict-{uuid.uuid4()}"
    body = _create_task_body(live_stack)
    body["idempotency_key"] = key
    first = http.post(
        f"{live_stack.worker_url}/internal/tasks", json=body, headers=_headers(live_stack, key)
    )
    assert first.status_code == 200, first.text

    mutated = {**body, "intent_key": "e2e_other_intent"}
    conflict = http.post(
        f"{live_stack.worker_url}/internal/tasks",
        json=mutated,
        headers=_headers(live_stack, key),
    )
    assert conflict.status_code in (409, 422), conflict.text
    assert conflict.json()["code"] in {"IDEMPOTENCY_MISMATCH", "COMMON_CONFLICT"}


def test_b141_schedule_creation_replays_without_duplicate(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    key = f"e2e-schedule-idem-{uuid.uuid4()}"
    resolved = load_resolved(live_stack)
    body = {
        "name": "e2e idempotent schedule",
        "agent_id": str(live_stack.agent_id),
        "actor_user_id": str(live_stack.platform_user_id),
        "intent_key": "e2e_policy_check",
        "skill_id": str(live_stack.skill_id),
        "input_template": {"customer": "A"},
        "schedule": {"type": "CRON", "cron": "0 9 * * *", "timezone": "Asia/Shanghai"},
        "delivery_route": {
            "channel": "WECOM",
            "bot_id": live_stack.bot_id,
            "external_user_id": "e2e-external-user",
        },
    }
    headers = _headers(live_stack, key)
    first = http.post(f"{live_stack.worker_url}/internal/schedules", json=body, headers=headers)
    assert first.status_code == 200, first.text
    replay = http.post(f"{live_stack.worker_url}/internal/schedules", json=body, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["schedule_id"] == first.json()["data"]["schedule_id"]
    assert resolved["skill"].key


def test_b141_runtime_client_submits_with_idempotency_key(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    context, resolved = submission_context(live_stack)

    async def submit() -> dict[str, Any]:
        client = WorkerTaskClient(live_stack.worker_url, service_token=INTERNAL_TOKEN)
        try:
            return await client.submit_task(
                context, skill=resolved["skill"], input_data={"case": "runtime-client"}
            )
        finally:
            await client.aclose()

    first = run_async(submit)
    second = run_async(submit)
    assert first["task_id"] == second["task_id"], "同 Run 同输入必须复用同一 Task"
    assert json.dumps(first)
