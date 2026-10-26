import asyncio
import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_runtime.application.run_service import snapshot_policy
from muad_agent_runtime.infrastructure.db import get_engine, get_session_factory
from muad_agent_runtime.infrastructure.models.async_tools import RunContinuation, ToolOperation
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    RunInterrupt,
    RunRecord,
    RuntimeSnapshot,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import CompletionMode, OperationStatus, ResolveDefinitionResponse
from sqlalchemy import event, select

from agent_runtime.conftest import FakeResolveClient, TenantContext, parse_sse
from tests.internal_service import service_headers


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


def _payload(tenant: TenantContext, text: str = "hello runtime") -> dict[str, Any]:
    return {
        "agent_id": str(tenant.agent_id),
        "platform_user_id": str(tenant.platform_user_id),
        "channel": {"type": "WECOM", "bot_id": "bot-1", "external_conversation_id": "ext-1"},
        "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": text},
    }


def _attachment_ref(storage_key: str) -> dict[str, Any]:
    """入站附件引用（形状来自 contracts；`kind=DOCUMENT` 不触发内联读盘）。"""
    return {
        "storage_key": storage_key,
        "kind": "DOCUMENT",
        "media_type": "text/plain",
        "size": 12,
        "filename": "note.txt",
        "checksum": "sha256:" + "b" * 64,
        "source_channel": "WECOM",
    }


def _expected_hash(resolved: ResolveDefinitionResponse) -> str:
    canonical = json.dumps(
        {
            "agent": resolved.agent.model_dump(mode="json"),
            # 08 TASK-004：Snapshot hash/model_json 剥离 api_key
            "model": {k: v for k, v in resolved.model.model_dump(mode="json").items() if k != "api_key"},
            "skills": [skill.model_dump(mode="json") for skill in resolved.skills],
            "mcp_servers": [server.model_dump(mode="json") for server in resolved.mcp_servers],
            # 真实创建路径冻结成员事实（B-224）：该租户的模型没有成员 ⇒ 空列表也在 hash 里
            "policy": snapshot_policy(members=()),
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
    entry_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        conversation = Conversation(
            tenant_id=tenant.tenant_id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            status="ACTIVE",
            last_seq=0,
            channel_account_id=entry_id,
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
            # 生产 Run 行总有受理时间：续跑的受理回执要把状态投影回给调用方（TASK-010）。
            start_time=datetime.now(UTC),
            deadline_at=datetime.now(UTC) + timedelta(minutes=2),
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
                # 执行期策略按生产口径冻结：resume 路径要求 `memory` 分组叶子完整
                # （`run_service._frozen_memory_policy`），手写残缺快照会被拒。
                policy_json=snapshot_policy(),
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
        assert run.lease_owner is None
        assert run.lease_until is None

        snapshot = await session.get(RuntimeSnapshot, run.snapshot_id)
        assert snapshot is not None
        assert snapshot.run_id == run_id
        assert snapshot.prompt_template_version == "1"
        # 真实创建路径的 policy_json = 直构策略 + 冻结的成员事实（该租户模型没有成员 ⇒ 空列表）
        assert snapshot.policy_json == snapshot_policy(members=())
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
            "run.stage",
            "run.stage",
            "message.delta",
            "message.delta",
            "message.delta",
            "run.completed",
        ]
        # 阶段事件是 Run 级事实（不属于某次提交），其余事件都带提交链接
        assert all(
            row.submission_id is not None for row in rows if row.stream_type != "run.stage"
        )
        assert all(row.submission_id is None for row in rows if row.stream_type == "run.stage")

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
        json={
            "platform_user_id": str(tenant.platform_user_id),
            "input": {"type": "text", "text": "answer"},
        },
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
        json={
            "platform_user_id": str(tenant.platform_user_id),
            "input": {"type": "text", "text": "answer"},
        },
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
        json={
            "platform_user_id": str(tenant.platform_user_id),
            "input": {"type": "text", "text": "answer"},
        },
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
        json={
            "platform_user_id": str(tenant.platform_user_id),
            "input": {"id": "reply-1", "type": "text", "text": "device 1"},
        },
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


async def test_create_run_same_key_with_a_different_attachment_conflicts(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    """[审查 2026-10-06] 同 key 换了附件 ⇒ `IDEMPOTENCY_MISMATCH`（不得当成同一请求重放）。

    指纹此前只覆盖文本与 message_id：换了附件会被静默丢掉（重放第一个 Run 的结果），用户以为
    自己把文件发出去了。
    """
    payload = _payload(tenant)
    payload["message"] = {
        **payload["message"],
        "type": "attachment",
        "attachments": [_attachment_ref("inbound/a/0")],
    }
    headers = {**_headers(tenant), "Idempotency-Key": "create-idem-attachment"}
    first = await client.post("/v1/runs", json=payload, headers=headers)
    assert first.status_code == 200, first.text

    changed = {
        **payload,
        "message": {
            **payload["message"],
            "attachments": [_attachment_ref("inbound/b/1")],
        },
    }
    second = await client.post("/v1/runs", json=changed, headers=headers)
    assert second.status_code == 409
    assert second.json()["code"] == "IDEMPOTENCY_MISMATCH"


async def test_create_run_same_key_for_another_recipient_conflicts(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    """[审查 2026-10-06] 同 key 换了接收方 ⇒ `IDEMPOTENCY_MISMATCH`。

    投递路由（`bot_id` / `external_user_id` / 外部会话）此前不在指纹里：同 key 的重放会把回复
    发给**上一个人**。
    """
    payload = _payload(tenant)
    headers = {**_headers(tenant), "Idempotency-Key": "create-idem-route"}
    first = await client.post("/v1/runs", json=payload, headers=headers)
    assert first.status_code == 200, first.text

    changed = {
        **payload,
        "channel": {**payload["channel"], "external_user_id": "another-user"},
    }
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


async def _seed_waiting_run(
    tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    *,
    status: str = "WAITING_TOOL",
    operations: Sequence[tuple[CompletionMode, OperationStatus]] = (),
    wait_generation: int = 0,
    ready: bool = False,
    with_interrupt: bool = False,
    waiting_event: int = 0,
) -> uuid.UUID:
    """种一个等待中的 Run：operation 依赖、检查点、等待起点事件/中断；返回 run_id。"""
    conversation_id, run_id = await _insert_run(
        tenant, status, with_interrupt=with_interrupt, resolved=resolved
    )
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            run = await session.get(RunRecord, run_id)
            assert run is not None and run.snapshot_id is not None
            for mode, op_status in operations:
                session.add(
                    ToolOperation(
                        tenant_id=tenant.tenant_id,
                        run_id=run_id,
                        actor_user_id=tenant.platform_user_id,
                        source_tool_call_id=f"call-{uuid.uuid4().hex[:8]}",
                        completion_mode=mode,
                        status=op_status,
                        task_snapshot_hash="sha256:" + "a" * 64,
                        submission_json={"input": "submission-canary"},
                        input_hash="sha256:" + "b" * 64,
                    )
                )
            if ready or wait_generation:
                session.add(
                    RunContinuation(
                        tenant_id=tenant.tenant_id,
                        run_id=run_id,
                        snapshot_id=run.snapshot_id,
                        wait_generation=wait_generation,
                        ready=ready,
                        context_upto_seq=0,
                        runner_state_json={},
                    )
                )
            if waiting_event:
                session.add(
                    CanonicalEvent(
                        tenant_id=tenant.tenant_id,
                        conversation_id=conversation_id,
                        run_id=run_id,
                        seq=waiting_event,
                        event_type="RUN_WAITING_TOOL",
                        stream_type="run.waiting_tool",
                        payload_json={"event_version": 1},
                    )
                )
    return run_id


async def test_get_run_reports_waiting_tool_projection(
    client: AsyncClient,
    tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
) -> None:
    """API-05：等待起点/原因与 pending 计数；只读投影不含 input/结果/提交 JSON。"""
    run_id = await _seed_waiting_run(
        tenant,
        resolved,
        operations=[
            (CompletionMode.JOIN, OperationStatus.SUBMIT_PENDING),
            (CompletionMode.DETACH, OperationStatus.SUBMIT_PENDING),
            (CompletionMode.JOIN, OperationStatus.TASK_ACCEPTED),
            (CompletionMode.JOIN, OperationStatus.COMPLETED),
        ],
        wait_generation=2,
        waiting_event=1,
    )

    response = await client.get(f"/v1/runs/{run_id}", headers=_headers(tenant))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "WAITING_TOOL"
    assert data["waiting_reason"] == "SUBMISSION"
    # 未终态 JOIN：SUBMIT_PENDING + TASK_ACCEPTED；COMPLETED 不再算依赖
    assert data["pending_join_count"] == 2
    assert data["pending_submission_count"] == 2
    assert data["continuation_count"] == 2
    assert data["waiting_since"] is not None
    assert data["deadline_at"] is not None
    assert "submission-canary" not in response.text
    assert "pending input" not in response.text


async def test_get_run_reports_resume_ready_and_task_result_reasons(
    client: AsyncClient,
    tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
) -> None:
    """结果已到但尚未 claim ⇒ RESUME_READY；只有待定 JOIN 任务 ⇒ TASK_RESULT。"""
    ready_run = await _seed_waiting_run(
        tenant,
        resolved,
        operations=[(CompletionMode.JOIN, OperationStatus.RESULT_RECEIVED)],
        wait_generation=1,
        ready=True,
    )
    task_run = await _seed_waiting_run(
        tenant,
        resolved,
        operations=[(CompletionMode.JOIN, OperationStatus.RUNNING)],
        wait_generation=1,
    )
    bare_run = await _seed_waiting_run(tenant, resolved)

    ready = (await client.get(f"/v1/runs/{ready_run}", headers=_headers(tenant))).json()["data"]
    assert ready["waiting_reason"] == "RESUME_READY"
    assert ready["pending_join_count"] == 1 and ready["pending_submission_count"] == 0

    pending = (await client.get(f"/v1/runs/{task_run}", headers=_headers(tenant))).json()["data"]
    assert pending["waiting_reason"] == "TASK_RESULT"
    assert pending["pending_join_count"] == 1 and pending["pending_submission_count"] == 0

    # 无任何依赖的 WAITING_TOOL（不应发生但必须安全）：原因与计数都为空，不编造等待对象
    bare = (await client.get(f"/v1/runs/{bare_run}", headers=_headers(tenant))).json()["data"]
    assert bare["waiting_reason"] is None
    assert bare["pending_join_count"] == 0 and bare["pending_submission_count"] == 0


async def test_get_run_waiting_input_and_terminal_have_no_waiting_reason(
    client: AsyncClient,
    tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
) -> None:
    """WAITING_INPUT 与终态都不套用 WAITING_TOOL 的原因；等待起点取等待中断时间。"""
    waiting_input = await _seed_waiting_run(
        tenant, resolved, status="WAITING_INPUT", with_interrupt=True
    )
    completed = await _seed_waiting_run(tenant, resolved, status="COMPLETED")

    input_data = (
        await client.get(f"/v1/runs/{waiting_input}", headers=_headers(tenant))
    ).json()["data"]
    assert input_data["status"] == "WAITING_INPUT"
    assert input_data["waiting_reason"] is None
    assert input_data["waiting_since"] is not None
    assert input_data["pending_join_count"] == 0 and input_data["pending_submission_count"] == 0
    assert input_data["continuation_count"] == 0

    done = (await client.get(f"/v1/runs/{completed}", headers=_headers(tenant))).json()["data"]
    assert done["waiting_reason"] is None and done["waiting_since"] is None


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


async def _insert_events(
    tenant: TenantContext,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    rows: list[tuple[int, str, str, dict[str, Any]]],
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        for seq, event_type, stream_type, payload in rows:
            session.add(
                CanonicalEvent(
                    tenant_id=tenant.tenant_id,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    seq=seq,
                    event_type=event_type,
                    stream_type=stream_type,
                    payload_json=payload,
                )
            )
        await session.commit()


async def test_run_events_endpoint_replays_after_confirmed_seq_and_closes_on_terminal(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    """API-04：GET 事件流从 `after_seq` 之后回放持久事件；终态排空后关闭，不重新执行。"""
    conversation_id, run_id = await _insert_run(tenant, "COMPLETED")
    await _insert_events(
        tenant,
        conversation_id,
        run_id,
        [
            (1, "RUN_CREATED", "run.created", {"resumed": False}),
            (2, "RUN_WAITING_TOOL", "run.waiting_tool", {"status": "WAITING_TOOL"}),
            (3, "RUN_RESUMED", "run.resumed", {"execution_epoch": 2}),
            (4, "RUN_COMPLETED", "run.completed", {"status": "COMPLETED", "final_text": "ok"}),
        ],
    )

    full = await client.get(f"/v1/runs/{run_id}/events", headers=_headers(tenant))
    assert full.status_code == 200
    assert full.headers["content-type"].startswith("text/event-stream")
    assert [event["type"] for event in parse_sse(full.text)] == [
        "run.created",
        "run.waiting_tool",
        "run.resumed",
        "run.completed",
    ]

    replay = await client.get(
        f"/v1/runs/{run_id}/events?after_seq=2", headers=_headers(tenant)
    )
    assert replay.status_code == 200
    events = parse_sse(replay.text)
    assert [event["type"] for event in events] == ["run.resumed", "run.completed"]
    assert [event["seq"] for event in events] == [3, 4]
    assert events[-1]["run_id"] == str(run_id)


async def test_run_events_endpoint_is_tenant_scoped_and_validates_after_seq(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    _, run_id = await _insert_run(tenant, "COMPLETED")
    other = await client.get(
        f"/v1/runs/{run_id}/events",
        headers={"X-Tenant-Id": f"other-{uuid.uuid4().hex}"},
    )
    assert other.status_code == 404

    negative = await client.get(
        f"/v1/runs/{run_id}/events?after_seq=-1", headers=_headers(tenant)
    )
    assert negative.status_code == 422


async def test_resume_endpoint_rejects_waiting_tool_run_with_run_busy(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    """显式人类 resume 只接受 WAITING_INPUT；工具等待由结果驱动接续，明确 RUN_BUSY。"""
    _, run_id = await _insert_run(tenant, "WAITING_TOOL")
    response = await client.post(
        f"/v1/runs/{run_id}/resume",
        json={
            "platform_user_id": str(tenant.platform_user_id),
            "input": {"type": "text", "text": "answer"},
        },
        headers={
            **_headers(tenant),
            "Idempotency-Key": f"resume-waiting-tool-{uuid.uuid4().hex[:6]}",
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "RUN_BUSY"


# --------------------------------------------------------------------------------------
# TASK-010 / S-15：WebChat 读路径（lean scope ≤2 SQL、202 后不 get_run、无 dead queue）
# --------------------------------------------------------------------------------------


async def _capture_sql(statements: list[str], call: Any) -> Any:
    """在真实 PG 引擎上记录一次调用的 SQL 语句（同步监听器只收集，不做任何改写）。"""
    engine = get_engine()

    def capture(*args: Any) -> None:
        statements.append(str(args[2]))

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        return await call()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)


def _scope_statements(statements: list[str]) -> list[str]:
    """归属解析的业务语句：只有 run/conversation 两表（`SELECT 1` 预检不算读路径）。"""
    return [
        statement
        for statement in statements
        if "runtime.run_record" in statement or "runtime.conversation" in statement
    ]


async def test_s15_run_scope_is_bounded_and_projects_owner_facts(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """API-07 打开流的归属解析：一次 JOIN（≤2 SQL），只投影核属所需的列。"""
    entry_id = uuid.uuid4()
    conversation_id, run_id = await _insert_run(tenant, "RUNNING", entry_id=entry_id)

    statements: list[str] = []

    async def fetch() -> Any:
        return await client.get(
            f"/v1/runs/{run_id}/scope",
            headers=service_headers(tenant.tenant_id),
            params={"user_id": str(tenant.platform_user_id)},
        )

    response = await _capture_sql(statements, fetch)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["run"] == {
        "run_id": str(run_id),
        "conversation_id": str(conversation_id),
        "user_id": str(tenant.platform_user_id),
    }
    assert data["conversation"] == {
        "conversation_id": str(conversation_id),
        "entry_id": str(entry_id),
        "agent_id": str(tenant.agent_id),
    }
    scope_sql = _scope_statements(statements)
    assert 1 <= len(scope_sql) <= 2, scope_sql

    denied_statements: list[str] = []

    async def fetch_denied() -> Any:
        return await client.get(
            f"/v1/runs/{run_id}/scope",
            headers=service_headers(tenant.tenant_id),
            params={"user_id": str(uuid.uuid4())},
        )

    denied = await _capture_sql(denied_statements, fetch_denied)
    assert denied.status_code == 404
    assert denied.json()["code"] == "COMMON_NOT_FOUND"
    assert len(_scope_statements(denied_statements)) <= 2, denied_statements


class _CountingOwner:
    """WebChat Owner 端口替身：只回答受理，任何 get_run 都是缺陷（计数后显式失败）。"""

    def __init__(self, admission: Any) -> None:
        self.admission = admission
        self.submit_calls: list[Any] = []
        self.get_run_calls = 0

    async def submit_run(self, request: Any, *, tenant_id: str, idempotency_key: str) -> Any:
        self.submit_calls.append((request, tenant_id, idempotency_key))
        return self.admission

    async def get_run(self, run_id: uuid.UUID, *, tenant_id: str) -> dict[str, Any]:
        self.get_run_calls += 1
        raise AssertionError("202 回执不得回读 Run（受理裁决必须自带 status/execution_status）")


class _Entries:
    def __init__(self, items: tuple[Any, ...]) -> None:
        self.items = items


def _chat_identity(tenant: TenantContext) -> Any:
    from muad_contracts.webchat import ChatAuthorizedAgent, ChatPrincipal
    from muad_im_gateway.application.chat_principal import ChatIdentity

    return ChatIdentity(
        principal=ChatPrincipal(
            tenant_id=tenant.tenant_id,
            platform_user_id=tenant.platform_user_id,
            session_id=uuid.uuid4(),
            auth_method="USER_TOKEN",
        ),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        agents=(ChatAuthorizedAgent(agent_id=tenant.agent_id, key="s15", name="S-15 Agent"),),
    )


async def test_s15_202_uses_the_admission_projection_and_never_reads_the_run(
    tenant: TenantContext,
) -> None:
    """提交（新 Run 与幂等重放）都直接用受理裁决的状态/阶段：202 之后不再 get_run。"""
    from muad_contracts import BotSnapshotItem, RunStatus
    from muad_contracts.webchat import WebchatExecutionStatus, WebchatMessageRequest
    from muad_im_gateway.application.runtime_client import RunAdmission
    from muad_im_gateway.application.webchat_service import WebchatResourceService

    entry_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    owner = _CountingOwner(
        RunAdmission(
            run_id=uuid.uuid4(),
            conversation_id=conversation_id,
            resumed=False,
            status=RunStatus.CREATED,
            execution_status=WebchatExecutionStatus(stage="RUNNING"),
        )
    )
    service = WebchatResourceService(
        runtime=owner,
        entries=_Entries(
            (
                BotSnapshotItem(
                    bot_account_id=entry_id,
                    bot_id="s15-webchat",
                    channel="WEBCHAT",
                    agent_id=tenant.agent_id,
                ),
            )
        ),
        tenant_id=tenant.tenant_id,
    )
    request = WebchatMessageRequest(
        conversation_id=conversation_id, message_id="msg-s15-core", text="S-15 提交"
    )

    accepted = await service.submit_message(
        _chat_identity(tenant), entry_id, request, idempotency_key="s15-key"
    )
    assert accepted.run_id == owner.admission.run_id
    assert accepted.conversation_id == conversation_id
    assert accepted.status == RunStatus.CREATED
    assert accepted.execution_status.stage == "RUNNING"
    submitted, tenant_id, key = owner.submit_calls[0]
    assert tenant_id == tenant.tenant_id and key == "s15-key"
    assert submitted.agent_id == tenant.agent_id
    assert submitted.channel.bot_id == str(entry_id)

    # 幂等重放：状态取自受理裁决（这里模拟已完成的 Run），仍然不需要 get_run。
    owner.admission = RunAdmission(
        run_id=owner.admission.run_id,
        conversation_id=conversation_id,
        resumed=False,
        status=RunStatus.COMPLETED,
        execution_status=WebchatExecutionStatus(stage="COMPLETED"),
    )
    replay = await service.submit_message(
        _chat_identity(tenant), entry_id, request, idempotency_key="s15-key"
    )
    assert replay.status == RunStatus.COMPLETED
    assert replay.execution_status.stage == "COMPLETED"
    assert owner.get_run_calls == 0


async def test_s15_webchat_adapter_has_no_dead_inbound_queue() -> None:
    """WEBCHAT 入站是 SYNC（HTTP→统一核心→Runtime）：适配器不再有进程内队列/消费循环。"""
    from muad_im_gateway.channels.webchat import WebchatChannelAdapter

    adapter = WebchatChannelAdapter()
    await adapter.start()
    try:
        events = await adapter.iter_events()
        with pytest.raises(StopAsyncIteration):
            # 旧实现的 `iter_events()` 会永远挂在空队列上：超时即失败，不把 RED 变成挂起。
            await asyncio.wait_for(anext(events), timeout=1.0)
        for attribute in ("submit", "pending", "max_pending"):
            assert not hasattr(adapter, attribute), attribute
    finally:
        await adapter.stop()
