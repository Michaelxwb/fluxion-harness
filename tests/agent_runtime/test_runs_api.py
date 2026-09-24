import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from conftest import FakeResolveClient, TenantContext, parse_sse
from httpx import AsyncClient
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    RunInterrupt,
    RunRecord,
    RuntimeSnapshot,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import ResolveDefinitionResponse
from sqlalchemy import select


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


def _payload(tenant: TenantContext, text: str = "hello runtime") -> dict[str, Any]:
    return {
        "agent_id": str(tenant.agent_id),
        "platform_user_id": str(tenant.platform_user_id),
        "channel": {"type": "WECOM", "bot_id": "bot-1", "external_conversation_id": "ext-1"},
        "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": text},
    }


def _expected_hash(resolved: ResolveDefinitionResponse) -> str:
    canonical = json.dumps(
        {
            "agent": resolved.agent.model_dump(mode="json"),
            # 08 TASK-004：Snapshot hash/model_json 剥离 api_key
            "model": {k: v for k, v in resolved.model.model_dump(mode="json").items() if k != "api_key"},
            "skills": [skill.model_dump(mode="json") for skill in resolved.skills],
            "mcp_servers": [server.model_dump(mode="json") for server in resolved.mcp_servers],
            "policy": {"max_model_retries": 3},
            "prompt_template_version": "1",
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _insert_run(
    tenant: TenantContext,
    status: str,
    *,
    lease_until: datetime | None = None,
    with_interrupt: bool = False,
    resolved: ResolveDefinitionResponse | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        conversation = Conversation(
            tenant_id=tenant.tenant_id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            status="ACTIVE",
            last_seq=0,
        )
        session.add(conversation)
        await session.flush()
        run = RunRecord(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            status=status,
            input_text="pending input",
            trace_id=uuid.uuid4().hex,
            cancel_requested=False,
            lease_until=lease_until,
        )
        session.add(run)
        await session.flush()
        if resolved is not None:
            snapshot = RuntimeSnapshot(
                tenant_id=tenant.tenant_id,
                run_id=run.id,
                schema_version=1,
                agent_revision=resolved.agent.revision,
                model_revision=resolved.model.revision,
                agent_json=resolved.agent.model_dump(mode="json"),
                model_json=resolved.model.model_dump(mode="json"),
                skill_catalog_json=[skill.model_dump(mode="json") for skill in resolved.skills],
                mcp_catalog_json=[server.model_dump(mode="json") for server in resolved.mcp_servers],
                policy_json={"max_model_retries": 3},
                prompt_template_version="1",
                content_hash="sha256:" + "0" * 64,
            )
            session.add(snapshot)
            await session.flush()
            run.snapshot_id = snapshot.id
        if with_interrupt:
            session.add(
                RunInterrupt(
                    tenant_id=tenant.tenant_id,
                    run_id=run.id,
                    conversation_id=conversation.id,
                    interrupt_type="CONFIRM",
                    prompt_text="continue?",
                    options_json=["yes", "no"],
                    status="WAITING",
                )
            )
        await session.commit()
        return conversation.id, run.id


async def test_create_run_streams_events_and_persists_records(
    client: AsyncClient,
    tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
) -> None:
    response = await client.post("/v1/runs", json=_payload(tenant), headers=_headers(tenant))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"

    events = parse_sse(response.text)
    assert events
    first = events[0]
    assert first["type"] == "run.created"
    # seq 1 是同一事务内落库的 USER_MESSAGE 业务事件；SSE 事件沿用持久化序号
    assert first["seq"] == 2
    assert first["data"]["resumed"] is False
    assert first["run_id"]
    assert uuid.UUID(first["data"]["conversation_id"])
    assert first["data"]["trace_id"]
    assert first["timestamp"]

    seqs = [event["seq"] for event in events]
    # seq 单调递增且沿用持久化序号（业务事件占号但不出现在 SSE，允许跳号）
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    types = [event["type"] for event in events]
    assert types.count("message.delta") == 3
    assert types[-1] == "run.completed"
    assert events[-1]["data"]["status"] == "COMPLETED"
    assert events[-1]["data"]["final_text"]

    run_id = uuid.UUID(first["run_id"])
    conversation_id = uuid.UUID(first["data"]["conversation_id"])

    async with get_session_factory()() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None
        assert run.status == "COMPLETED"
        assert run.end_time is not None
        assert run.snapshot_id is not None
        assert run.lease_owner
        assert run.lease_until is not None

        snapshot = await session.get(RuntimeSnapshot, run.snapshot_id)
        assert snapshot is not None
        assert snapshot.run_id == run_id
        assert snapshot.prompt_template_version == "1"
        assert snapshot.policy_json == {"max_model_retries": 3}
        assert snapshot.agent_revision == resolved.agent.revision
        assert snapshot.model_revision == resolved.model.revision
        assert snapshot.agent_json["key"] == resolved.agent.key
        assert snapshot.skill_catalog_json[0]["key"] == resolved.skills[0].key
        assert snapshot.mcp_catalog_json[0]["key"] == resolved.mcp_servers[0].key
        assert snapshot.content_hash == _expected_hash(resolved)

        rows = (
            await session.scalars(
                select(CanonicalEvent)
                .where(CanonicalEvent.conversation_id == conversation_id)
                .order_by(CanonicalEvent.seq)
            )
        ).all()
        business = [row for row in rows if row.stream_type is None]
        streamed = [row for row in rows if row.stream_type is not None]
        assert [row.event_type for row in business] == ["USER_MESSAGE", "ASSISTANT_MESSAGE"]
        assert [row.seq for row in business] == [1, rows[-2].seq]
        assert business[0].payload_json["text"] == "hello runtime"
        assert [row.stream_type for row in streamed] == [
            "run.created",
            "message.delta",
            "message.delta",
            "message.delta",
            "run.completed",
        ]
        assert all(row.submission_id is not None for row in rows)

        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.last_seq == len(rows)
        assert conversation.last_run_id == run_id


async def test_create_run_returns_busy_when_active_run_exists(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    await _insert_run(tenant, "RUNNING")
    response = await client.post("/v1/runs", json=_payload(tenant), headers=_headers(tenant))
    assert response.status_code == 409
    assert response.json()["code"] == "RUN_BUSY"

    created = await _insert_run(tenant, "CREATED")
    created_response = await client.post(
        "/v1/runs",
        json={**_payload(tenant), "conversation_id": str(created[0])},
        headers=_headers(tenant),
    )
    assert created_response.status_code == 409
    assert created_response.json()["code"] == "RUN_BUSY"


async def test_create_run_auto_resumes_waiting_input_run(
    client: AsyncClient,
    tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
) -> None:
    conversation_id, run_id = await _insert_run(
        tenant, "WAITING_INPUT", with_interrupt=True, resolved=resolved
    )
    response = await client.post(
        "/v1/runs",
        json=_payload(tenant, "choose device 1"),
        headers=_headers(tenant),
    )
    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[0]["type"] == "run.created"
    assert events[0]["data"]["resumed"] is True
    assert events[0]["run_id"] == str(run_id)
    assert events[-1]["type"] == "run.completed"
    assert events[-1]["data"]["status"] == "COMPLETED"

    async with get_session_factory()() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None
        assert run.status == "COMPLETED"

        interrupt = await session.scalar(select(RunInterrupt).where(RunInterrupt.run_id == run_id))
        assert interrupt is not None
        assert interrupt.status == "RESOLVED"
        assert interrupt.resolution_json == {"input": "choose device 1"}
        assert interrupt.resolved_at is not None

        rows = (
            await session.scalars(
                select(CanonicalEvent)
                .where(CanonicalEvent.conversation_id == conversation_id)
                .order_by(CanonicalEvent.seq)
            )
        ).all()
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.last_seq == len(rows)


async def test_resume_terminal_run_returns_current_state_idempotently(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    """API-02：已终态 Run 返回当前终态（幂等），不再执行。"""
    _, run_id = await _insert_run(tenant, "COMPLETED")
    response = await client.post(
        f"/v1/runs/{run_id}/resume",
        json={"input": {"type": "text", "text": "answer"}},
        headers={**_headers(tenant), "Idempotency-Key": "resume-terminal-1"},
    )
    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[-1]["type"] == "run.completed"
    assert events[-1]["data"]["status"] == "COMPLETED"


async def test_resume_endpoint_rejects_running_run(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    _, run_id = await _insert_run(tenant, "RUNNING")
    response = await client.post(
        f"/v1/runs/{run_id}/resume",
        json={"input": {"type": "text", "text": "answer"}},
        headers={**_headers(tenant), "Idempotency-Key": "resume-running-1"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "REVISION_CONFLICT"


async def test_resume_without_key_or_input_id_is_validation_error(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    _, run_id = await _insert_run(tenant, "WAITING_INPUT")
    response = await client.post(
        f"/v1/runs/{run_id}/resume",
        json={"input": {"type": "text", "text": "answer"}},
        headers=_headers(tenant),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_resume_endpoint_completes_waiting_input_run(
    client: AsyncClient,
    tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
) -> None:
    _, run_id = await _insert_run(tenant, "WAITING_INPUT", with_interrupt=True, resolved=resolved)
    response = await client.post(
        f"/v1/runs/{run_id}/resume",
        json={"input": {"id": "reply-1", "type": "text", "text": "device 1"}},
        headers=_headers(tenant),
    )
    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[0]["data"]["resumed"] is True
    assert events[-1]["data"]["status"] == "COMPLETED"

    async with get_session_factory()() as session:
        interrupt = await session.scalar(select(RunInterrupt).where(RunInterrupt.run_id == run_id))
        assert interrupt is not None
        assert interrupt.status == "RESOLVED"


async def test_create_run_idempotency_key_replays_persisted_stream(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    """[B-01] 同 key 同指纹：200 重放已持久化事件，不创建新 Run/不二次执行。"""
    payload = _payload(tenant)
    headers = {**_headers(tenant), "Idempotency-Key": "create-idem-1"}
    first = await client.post("/v1/runs", json=payload, headers=headers)
    assert first.status_code == 200
    first_events = parse_sse(first.text)

    second = await client.post("/v1/runs", json=payload, headers=headers)
    assert second.status_code == 200
    second_events = parse_sse(second.text)
    assert [event["seq"] for event in second_events] == [
        event["seq"] for event in first_events
    ]
    assert [event["type"] for event in second_events] == [
        event["type"] for event in first_events
    ]

    async with get_session_factory()() as session:
        run_id = uuid.UUID(first_events[0]["run_id"])
        count = await session.scalar(
            select(sa.func.count())
            .select_from(RunRecord)
            .where(RunRecord.tenant_id == tenant.tenant_id)
        )
        assert count == 1
        run = await session.get(RunRecord, run_id)
        assert run is not None


async def test_create_run_same_key_different_fingerprint_conflicts(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    payload = _payload(tenant)
    headers = {**_headers(tenant), "Idempotency-Key": "create-idem-2"}
    first = await client.post("/v1/runs", json=payload, headers=headers)
    assert first.status_code == 200

    changed = {**payload, "message": {**payload["message"], "text": "different text"}}
    second = await client.post("/v1/runs", json=changed, headers=headers)
    assert second.status_code == 409
    assert second.json()["code"] == "IDEMPOTENCY_MISMATCH"


async def test_cancel_active_without_active_run_returns_not_found(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    response = await client.post(
        "/v1/runs/cancel-active",
        json={"agent_id": str(tenant.agent_id), "platform_user_id": str(tenant.platform_user_id)},
        headers=_headers(tenant),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NO_ACTIVE_RUN"


async def test_cancel_active_cancels_waiting_input_run_immediately(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    _, run_id = await _insert_run(tenant, "WAITING_INPUT", with_interrupt=True)
    response = await client.post(
        "/v1/runs/cancel-active",
        json={"agent_id": str(tenant.agent_id), "platform_user_id": str(tenant.platform_user_id)},
        headers=_headers(tenant),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data == {"run_id": str(run_id), "status": "CANCELLED"}

    async with get_session_factory()() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None
        assert run.status == "CANCELLED"
        assert run.end_time is not None


async def test_cancel_run_is_idempotent_for_terminal_run(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    _, run_id = await _insert_run(tenant, "COMPLETED")
    response = await client.post(f"/v1/runs/{run_id}/cancel", headers=_headers(tenant))
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "COMPLETED"


async def test_get_run_returns_status_and_snapshot_summary(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    started = await client.post("/v1/runs", json=_payload(tenant), headers=_headers(tenant))
    assert started.status_code == 200, started.text
    run_id = parse_sse(started.text)[0]["run_id"]

    response = await client.get(f"/v1/runs/{run_id}", headers=_headers(tenant))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["run_id"] == run_id
    assert data["status"] == "COMPLETED"
    assert data["cancel_requested"] is False
    assert data["trace_id"]
    assert data["snapshot"]["content_hash"].startswith("sha256:")
    assert data["snapshot"]["prompt_template_version"] == "1"
    serialized = json.dumps(data)
    assert "api_key" not in serialized
    assert "model_json" not in serialized

    missing = await client.get(f"/v1/runs/{uuid.uuid4()}", headers=_headers(tenant))
    assert missing.status_code == 404
    assert missing.json()["code"] == "COMMON_NOT_FOUND"


async def test_create_conversation_returns_conversation(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    response = await client.post(
        "/v1/conversations",
        json={"agent_id": str(tenant.agent_id), "platform_user_id": str(tenant.platform_user_id)},
        headers=_headers(tenant),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["agent_id"] == str(tenant.agent_id)
    assert data["user_id"] == str(tenant.platform_user_id)
    assert data["status"] == "ACTIVE"
    assert data["last_seq"] == 0
    assert data["last_run_id"] is None


async def test_create_conversation_replays_by_idempotency_key(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    """design §3.4.2：同 key 同指纹重放首次会话；异指纹 409；无 key 仍每次新建。"""
    body = {"agent_id": str(tenant.agent_id), "platform_user_id": str(tenant.platform_user_id)}
    key = f"conv-{uuid.uuid4().hex[:8]}"
    keyed = {**_headers(tenant), "Idempotency-Key": key}

    first = await client.post("/v1/conversations", json=body, headers=keyed)
    replay = await client.post("/v1/conversations", json=body, headers=keyed)
    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert first.json()["data"]["conversation_id"] == replay.json()["data"]["conversation_id"]

    mismatch = await client.post(
        "/v1/conversations",
        json={"agent_id": str(tenant.agent_id), "platform_user_id": str(uuid.uuid4())},
        headers=keyed,
    )
    assert mismatch.status_code == 409, mismatch.text
    assert mismatch.json()["code"] == "IDEMPOTENCY_MISMATCH"

    without_key = await client.post("/v1/conversations", json=body, headers=_headers(tenant))
    assert without_key.status_code == 200
    assert (
        without_key.json()["data"]["conversation_id"]
        != first.json()["data"]["conversation_id"]
    )


async def test_resolve_error_propagates_as_envelope(
    client: AsyncClient,
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    fake_resolve.error = AppError(ErrorCode.AGENT_ACCESS_DENIED)
    response = await client.post("/v1/runs", json=_payload(tenant), headers=_headers(tenant))
    assert response.status_code == 403
    assert response.json()["code"] == "AGENT_ACCESS_DENIED"
