"""[B-141][B-201][E-07] 真实 Worker HTTP 的提交幂等与冲突。"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
from muad_agent_runtime.application.task_client import WorkerTaskClient

from .environment import INTERNAL_TOKEN, LiveStack, run_async
from .helpers import load_resolved, submission_context


def _headers(live_stack: LiveStack, idempotency_key: str) -> dict[str, str]:
    return {**live_stack.service_headers(), "Idempotency-Key": idempotency_key}


def _create_task_body(live_stack: LiveStack) -> dict[str, Any]:
    resolved = load_resolved(live_stack)
    snapshot, snapshot_hash = _snapshot(live_stack)
    return {
        "tenant_id": live_stack.tenant_id,
        "agent_id": str(live_stack.agent_id),
        "actor_user_id": str(live_stack.platform_user_id),
        "task_type": "SKILL",
        "skill_id": str(resolved["skill"].skill_id),
        "skill_artifact_id": str(resolved["skill"].artifact_id),
        "skill_execution_mode": "ASYNC",
        "input": {},
        "execution_snapshot": snapshot,
        "execution_snapshot_schema_version": 4,
        "snapshot_hash": snapshot_hash,
        "idempotency_key": "placeholder",
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

    first = http.post(f"{live_stack.worker_url}/internal/tasks", json=body, headers=_headers(live_stack, key))
    assert first.status_code == 200, first.text
    first_task = first.json()["data"]["task_id"]

    replay = http.post(
        f"{live_stack.worker_url}/internal/tasks", json=body, headers=_headers(live_stack, key)
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["task_id"] == first_task


def test_b141_same_key_different_fingerprint_conflicts(live_stack: LiveStack, http: httpx.Client) -> None:
    key = f"e2e-idem-conflict-{uuid.uuid4()}"
    body = _create_task_body(live_stack)
    body["idempotency_key"] = key
    first = http.post(f"{live_stack.worker_url}/internal/tasks", json=body, headers=_headers(live_stack, key))
    assert first.status_code == 200, first.text

    mutated = {**body, "input": {"case": "other-fingerprint"}}
    conflict = http.post(
        f"{live_stack.worker_url}/internal/tasks",
        json=mutated,
        headers=_headers(live_stack, key),
    )
    assert conflict.status_code in (409, 422), conflict.text
    assert conflict.json()["code"] in {"IDEMPOTENCY_MISMATCH", "COMMON_CONFLICT"}


def test_b141_schedule_creation_replays_without_duplicate(live_stack: LiveStack, http: httpx.Client) -> None:
    """v2 创建入口是 Draft + 确认：两个端点都按 Idempotency-Key 原样重放。"""
    draft_key = f"e2e-draft-idem-{uuid.uuid4()}"
    confirm_key = f"e2e-confirm-idem-{uuid.uuid4()}"
    identity = {
        **live_stack.service_headers(),
        "X-Actor-User-Id": str(live_stack.platform_user_id),
        "X-Agent-Id": str(live_stack.agent_id),
        "X-Entry-Channel": "WECOM",
    }
    body = {
        "instruction": "e2e idempotent schedule",
        "recurrence": {"kind": "CRON", "cron": "0 9 * * *", "timezone": "Asia/Shanghai"},
        "payload": {
            "kind": "SKILL_PLAN",
            "skill_id": str(live_stack.skill_id),
            "input": {"customer": "A"},
            "argv": [],
            "window": {"kind": "DAY", "offset": 0},
        },
        "delivery": {
            "intent": "PUSH",
            "route": {
                "channel": "WECOM",
                "bot_id": live_stack.bot_id,
                "external_user_id": "e2e-external-user",
            },
        },
    }
    draft_headers = {**identity, "Idempotency-Key": draft_key}
    first = http.post(
        f"{live_stack.worker_url}/internal/schedule-drafts", json=body, headers=draft_headers
    )
    assert first.status_code == 200, first.text
    draft = first.json()["data"]
    replay = http.post(
        f"{live_stack.worker_url}/internal/schedule-drafts", json=body, headers=draft_headers
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["draft_id"] == draft["draft_id"]

    confirm_headers = {
        **identity,
        "Idempotency-Key": confirm_key,
        "X-Confirmation-Surface": "WEBCHAT",
        "X-Confirmation-Ref": f"e2e-confirm-{uuid.uuid4()}",
    }
    confirm_body = {"draft_hash": draft["draft_hash"], "name": "e2e idempotent schedule"}
    confirmed = http.post(
        f"{live_stack.worker_url}/internal/schedule-drafts/{draft['draft_id']}/confirm",
        json=confirm_body,
        headers=confirm_headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    schedule_id = confirmed.json()["data"]["schedule_id"]
    confirmed_replay = http.post(
        f"{live_stack.worker_url}/internal/schedule-drafts/{draft['draft_id']}/confirm",
        json=confirm_body,
        headers=confirm_headers,
    )
    assert confirmed_replay.status_code == 200, confirmed_replay.text
    assert confirmed_replay.json()["data"]["schedule_id"] == schedule_id


def test_b141_runtime_client_submits_with_idempotency_key(live_stack: LiveStack, http: httpx.Client) -> None:
    context, resolved = submission_context(live_stack)

    async def submit() -> dict[str, Any]:
        client = WorkerTaskClient(live_stack.worker_url, service_token=INTERNAL_TOKEN)
        try:
            return await client.submit_task(
                context,
                call_id="standalone-call",
                skill=resolved["skill"],
                input_data={"case": "runtime-client"},
            )
        finally:
            await client.aclose()

    first = run_async(submit)
    second = run_async(submit)
    assert first["task_id"] == second["task_id"], "同 Run 同输入必须复用同一 Task"
    assert json.dumps(first)
