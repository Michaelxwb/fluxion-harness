import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

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
    assert first["seq"] == 1
    assert first["data"]["resumed"] is False
    assert first["run_id"]
    assert uuid.UUID(first["data"]["conversation_id"])
    assert first["data"]["trace_id"]
    assert first["timestamp"]

    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
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
        assert [row.event_type for row in rows] == ["USER_MESSAGE", "ASSISTANT_MESSAGE"]
        assert [row.seq for row in rows] == [1, 2]
        assert rows[0].payload_json["text"] == "hello runtime"

        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.last_seq == 2
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

        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.last_seq == 2


async def test_resume_endpoint_rejects_non_waiting_input_run(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    _, run_id = await _insert_run(tenant, "COMPLETED")
    response = await client.post(
        f"/v1/runs/{run_id}/resume",
        json={"input": {"type": "text", "text": "answer"}},
        headers=_headers(tenant),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "COMMON_CONFLICT"


async def test_resume_endpoint_completes_waiting_input_run(
    client: AsyncClient,
    tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
) -> None:
    _, run_id = await _insert_run(tenant, "WAITING_INPUT", with_interrupt=True, resolved=resolved)
    response = await client.post(
        f"/v1/runs/{run_id}/resume",
        json={"input": {"type": "text", "text": "device 1"}},
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


async def test_resolve_error_propagates_as_envelope(
    client: AsyncClient,
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    fake_resolve.error = AppError(ErrorCode.AGENT_ACCESS_DENIED)
    response = await client.post("/v1/runs", json=_payload(tenant), headers=_headers(tenant))
    assert response.status_code == 403
    assert response.json()["code"] == "AGENT_ACCESS_DENIED"
