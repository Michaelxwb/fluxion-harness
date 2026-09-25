"""[S-03][API-03/API-04] Runtime Admin Run 列表/详情内部端点。

真实边界：真实内部 HTTP（Runtime ASGI，`X-Internal-Service` 身份）→
`AdminRunService` → 参数化 SQL → 真实 PostgreSQL（`runtime.*` 四类事实 + `control.*` 名称权威行）。
不 Mock 业务 API：审计事实经真实写入 port（`RuntimeAuditWriter` / `EventWriter`）落库后由端点回读。

S-03 的端到端 Browser→Console→Runtime 场景由 TASK-017 承担；本文件验证 Runtime 端点自身的契约。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from conftest import TenantContext
from httpx import AsyncClient
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Artifact,
    Conversation,
    EgressAudit,
    ModelInvocationAudit,
    RunRecord,
    RuntimeSnapshot,
    ToolCallAudit,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

INTERNAL_HEADER = "X-Internal-Service"
TENANT_HEADER = "X-Tenant-Id"
TOKEN = "test-internal-service-token"

RUN_FIELDS = {
    "run_id",
    "conversation_id",
    "agent_id",
    "agent_name",
    "user_id",
    "user_name",
    "status",
    "trace_id",
    "cancel_requested",
    "error_code",
    "start_time",
    "end_time",
}
SNAPSHOT_FIELDS = {
    "snapshot_id",
    "schema_version",
    "agent_revision",
    "model_revision",
    "prompt_template_version",
    "content_hash",
}
LIST_ITEM_FIELDS = {
    "run_id",
    "conversation_id",
    "agent_id",
    "agent_name",
    "user_id",
    "user_name",
    "status",
    "trace_id",
    "start_time",
    "end_time",
    "error_code",
}
TIMELINE_FIELDS = {"seq", "event_type", "payload", "create_time"}
TOOL_AUDIT_FIELDS = {"audit_id", "tool_name", "tool_kind", "status", "latency_ms", "error_code"}
EGRESS_AUDIT_FIELDS = {
    "audit_id",
    "target_type",
    "target",
    "operation",
    "policy_decision",
    "result_status",
    "latency_ms",
    "error_code",
}
MODEL_AUDIT_FIELDS = {
    "audit_id",
    "provider",
    "model",
    "attempt",
    "status",
    "input_tokens",
    "output_tokens",
    "latency_ms",
    "error_code",
}
ARTIFACT_FIELDS = {
    "artifact_id",
    "artifact_type",
    "media_type",
    "size",
    "checksum",
    "preview",
    "create_time",
}
DETAIL_SECTIONS = {
    "run",
    "snapshot",
    "timeline",
    "tool_audits",
    "egress_audits",
    "model_invocations",
    "artifacts",
}

# 刻意种入的"像 Secret 的值"与"原始 Prompt"标记：响应中任何一处出现即判定泄露
SECRET_ARGS = "sk-live-args-secret-0001"
SECRET_MODEL = "sk-live-model-secret-0002"
RAW_INSTRUCTIONS = "RAW-SYSTEM-PROMPT-MARKER"
RAW_INPUT = "RAW-USER-PROMPT-MARKER"
LEAK_MARKERS = (SECRET_ARGS, SECRET_MODEL, RAW_INSTRUCTIONS, RAW_INPUT, "sk-live")

_INSERT_MODEL = text(
    """
    INSERT INTO control.model_definition
        (id, tenant_id, key, name, protocol, model_id, base_url, params_json, revision, enabled)
    VALUES (:model_id, :tenant_id, :key, :name, 'OPENAI', 'gpt-4o-mini',
            'https://api.example.com/v1', '{}'::jsonb, 1, true)
    """
)

_INSERT_AGENT = text(
    """
    INSERT INTO control.agent_definition
        (id, tenant_id, key, name, description, instructions, model_id,
         runtime_config_json, revision, enabled)
    VALUES (:agent_id, :tenant_id, :key, :name, NULL, '', :model_id,
            '{}'::jsonb, 1, true)
    """
)

_INSERT_PLATFORM_USER = text(
    """
    INSERT INTO control.platform_user (id, tenant_id, user_code, display_name, status)
    VALUES (:user_id, :tenant_id, :user_code, :display_name, 'ACTIVE')
    """
)


@dataclass(frozen=True)
class Seed:
    """一个租户内的名称权威行与本次种入的 Run 事实。"""

    run_ids: list[uuid.UUID]
    skill_id: uuid.UUID
    agent_name: str
    user_name: str


@pytest.fixture(autouse=True)
def internal_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)


@pytest.fixture(autouse=True)
async def clean_audit_tables(tenant: TenantContext) -> AsyncIterator[None]:
    """conftest 的清理链未覆盖三张审计表与 artifact，本文件自行收口。"""
    yield
    async with get_session_factory()() as session:
        for model in (ToolCallAudit, EgressAudit, ModelInvocationAudit, Artifact):
            await session.execute(model.__table__.delete().where(model.tenant_id == tenant.tenant_id))
        await session.execute(
            text("DELETE FROM control.agent_definition WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant.tenant_id},
        )
        await session.execute(
            text("DELETE FROM control.platform_user WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant.tenant_id},
        )
        await session.execute(
            text("DELETE FROM control.model_definition WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant.tenant_id},
        )
        await session.commit()


def _admin_headers(tenant_id: str) -> dict[str, str]:
    return {INTERNAL_HEADER: TOKEN, TENANT_HEADER: tenant_id}


async def _seed_authoritative_names(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    agent_id: uuid.UUID,
    agent_name: str,
) -> None:
    """control.* 是 agent/user 显示名的权威表（runtime 审计表不冗余名称）。"""
    model_id = uuid.uuid4()
    await session.execute(
        _INSERT_MODEL,
        {
            "model_id": model_id,
            "tenant_id": tenant.tenant_id,
            "key": f"admin-run-model-{model_id.hex[:8]}",
            "name": "Admin Run Model",
        },
    )
    await session.execute(
        _INSERT_AGENT,
        {
            "agent_id": agent_id,
            "tenant_id": tenant.tenant_id,
            "key": f"admin-run-agent-{agent_id.hex[:8]}",
            "name": agent_name,
            "model_id": model_id,
        },
    )


async def _seed_platform_user(
    session: AsyncSession, tenant: TenantContext, *, user_id: uuid.UUID, user_name: str
) -> None:
    await session.execute(
        _INSERT_PLATFORM_USER,
        {
            "user_id": user_id,
            "tenant_id": tenant.tenant_id,
            "user_code": f"admin-run-user-{user_id.hex[:8]}",
            "display_name": user_name,
        },
    )


async def _seed_run(
    tenant: TenantContext,
    *,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    status: str,
    start_time: datetime,
    end_time: datetime | None,
    error_code: str | None = None,
    input_text: str = "admin run seed",
    skill_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """落库 conversation + run_record（+ 可选 snapshot），返回 (conversation_id, run_id)。"""
    async with get_session_factory()() as session:
        conversation = Conversation(
            tenant_id=tenant.tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            status="ACTIVE",
            last_seq=0,
        )
        session.add(conversation)
        await session.flush()
        run = RunRecord(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            user_id=user_id,
            agent_id=agent_id,
            status=status,
            input_text=input_text,
            trace_id=uuid.uuid4().hex,
            cancel_requested=False,
            start_time=start_time,
            end_time=end_time,
            error_code=error_code,
        )
        session.add(run)
        await session.flush()
        if skill_id is not None:
            snapshot = _snapshot_row(tenant, run.id, skill_id)
            session.add(snapshot)
            await session.flush()
            run.snapshot_id = snapshot.id
        await session.commit()
        return conversation.id, run.id


def _snapshot_row(tenant: TenantContext, run_id: uuid.UUID, skill_id: uuid.UUID) -> RuntimeSnapshot:
    """冻结快照行：真实快照的 model_json 含模型密钥，端点只能投影摘要、不得透传。"""
    return RuntimeSnapshot(
        tenant_id=tenant.tenant_id,
        run_id=run_id,
        schema_version=1,
        agent_revision=3,
        model_revision=2,
        agent_json={"key": "admin-run-agent", "instructions": RAW_INSTRUCTIONS},
        model_json={"model_id": "gpt-4o-mini", "api_key": SECRET_MODEL},
        skill_catalog_json=[{"skill_id": str(skill_id), "key": "policy-check"}],
        mcp_catalog_json=[],
        policy_json={},
        prompt_template_version="1",
        content_hash="sha256:" + "a" * 64,
    )


async def _seed_list_authorities(
    tenant: TenantContext, *, other_agent: uuid.UUID, agent_name: str, user_name: str
) -> None:
    """列表用例的 control.* 权威行：两个 Agent + 一个平台用户（显示名用于断言）。"""
    async with get_session_factory()() as session:
        await _seed_authoritative_names(
            session, tenant, agent_id=tenant.agent_id, agent_name=agent_name
        )
        await _seed_authoritative_names(session, tenant, agent_id=other_agent, agent_name="审批助手")
        await _seed_platform_user(
            session, tenant, user_id=tenant.platform_user_id, user_name=user_name
        )
        await session.commit()


async def _seed_list_runs(
    tenant: TenantContext, *, other_agent: uuid.UUID, skill_id: uuid.UUID, now: datetime
) -> list[uuid.UUID]:
    """三个 Run（不同状态/时间/Agent，其中一个带 Skill 快照），按 start_time 升序返回。"""
    oldest = await _seed_run(
        tenant,
        agent_id=tenant.agent_id,
        user_id=tenant.platform_user_id,
        status="COMPLETED",
        start_time=now - timedelta(hours=3),
        end_time=now - timedelta(hours=3) + timedelta(minutes=1),
    )
    middle = await _seed_run(
        tenant,
        agent_id=other_agent,
        user_id=tenant.platform_user_id,
        status="FAILED",
        start_time=now - timedelta(hours=2),
        end_time=now - timedelta(hours=2) + timedelta(minutes=1),
        error_code="MODEL_UNAVAILABLE",
        skill_id=skill_id,
    )
    newest = await _seed_run(
        tenant,
        agent_id=tenant.agent_id,
        user_id=tenant.platform_user_id,
        status="RUNNING",
        start_time=now - timedelta(hours=1),
        end_time=None,
    )
    return [oldest[1], middle[1], newest[1]]


async def _seed_list_facts(tenant: TenantContext) -> Seed:
    """三个 Run（不同状态/时间/Agent）+ 一个带 Skill 快照的 Run，用于筛选与排序断言。"""
    now = datetime.now(UTC)
    agent_name = "策略检查助手"
    user_name = "张三"
    other_agent = uuid.uuid4()
    skill_id = uuid.uuid4()

    await _seed_list_authorities(
        tenant, other_agent=other_agent, agent_name=agent_name, user_name=user_name
    )
    run_ids = await _seed_list_runs(tenant, other_agent=other_agent, skill_id=skill_id, now=now)
    return Seed(
        run_ids=run_ids,
        skill_id=skill_id,
        agent_name=agent_name,
        user_name=user_name,
    )


async def _list_runs(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    """调用列表端点（默认分页），校验 200 后返回响应封套。"""
    listed = await client.get("/internal/admin/runs", headers=headers)
    assert listed.status_code == 200, listed.text
    return listed.json()


def _assert_list_envelope(body: dict[str, Any]) -> dict[str, Any]:
    assert body["code"] == "0"
    assert set(body) >= {"code", "msg", "data", "trace_id", "request_id", "timestamp"}
    return body["data"]


def _assert_default_list_page(data: dict[str, Any], seed: Seed) -> None:
    oldest, middle, newest = seed.run_ids
    assert set(data) == {"items", "page", "page_size", "total"}
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total"] == 3
    # ORDER BY start_time DESC
    assert [item["run_id"] for item in data["items"]] == [str(newest), str(middle), str(oldest)]
    for item in data["items"]:
        assert set(item) == LIST_ITEM_FIELDS
    newest_item, middle_item = data["items"][0], data["items"][1]
    assert newest_item["agent_name"] == seed.agent_name
    assert newest_item["user_name"] == seed.user_name
    assert newest_item["status"] == "RUNNING"
    assert newest_item["end_time"] is None
    assert middle_item["error_code"] == "MODEL_UNAVAILABLE"
    # RFC3339（内部响应），非 Console 展示格式
    assert datetime.fromisoformat(newest_item["start_time"]).tzinfo is not None


async def _assert_status_filter_narrows(
    client: AsyncClient, headers: dict[str, str], *, middle: uuid.UUID
) -> None:
    by_status = await client.get(
        "/internal/admin/runs", headers=headers, params={"status": "FAILED"}
    )
    assert by_status.status_code == 200, by_status.text
    assert [item["run_id"] for item in by_status.json()["data"]["items"]] == [str(middle)]


async def _assert_skill_filter_narrows(
    client: AsyncClient, headers: dict[str, str], *, middle: uuid.UUID, skill_id: uuid.UUID
) -> None:
    by_skill = await client.get(
        "/internal/admin/runs", headers=headers, params={"skill_id": str(skill_id)}
    )
    assert by_skill.status_code == 200, by_skill.text
    assert [item["run_id"] for item in by_skill.json()["data"]["items"]] == [str(middle)]


async def _assert_user_filter_keeps_total(
    client: AsyncClient, headers: dict[str, str], *, user_id: uuid.UUID
) -> None:
    by_user = await client.get(
        "/internal/admin/runs",
        headers=headers,
        params={"user_id": str(user_id)},
    )
    assert by_user.json()["data"]["total"] == 3


async def _assert_time_window_filter_narrows(
    client: AsyncClient, headers: dict[str, str], *, middle: uuid.UUID, newest: uuid.UUID
) -> None:
    by_time = await client.get(
        "/internal/admin/runs",
        headers=headers,
        params={
            "start_time": (datetime.now(UTC) - timedelta(hours=2, minutes=30)).isoformat(),
            "end_time": datetime.now(UTC).isoformat(),
        },
    )
    assert [item["run_id"] for item in by_time.json()["data"]["items"]] == [
        str(newest),
        str(middle),
    ]


async def _assert_pagination_splits_pages(
    client: AsyncClient, headers: dict[str, str], *, oldest: uuid.UUID
) -> None:
    first_page = await client.get(
        "/internal/admin/runs", headers=headers, params={"page": 1, "page_size": 2}
    )
    first_data = first_page.json()["data"]
    assert first_data["total"] == 3
    assert first_data["page_size"] == 2
    assert len(first_data["items"]) == 2
    second_page = await client.get(
        "/internal/admin/runs", headers=headers, params={"page": 2, "page_size": 2}
    )
    second_data = second_page.json()["data"]
    assert second_data["total"] == 3
    assert [item["run_id"] for item in second_data["items"]] == [str(oldest)]


async def _assert_invalid_query_params_rejected(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    too_large = await client.get(
        "/internal/admin/runs", headers=headers, params={"page_size": 101}
    )
    assert too_large.status_code == 422
    assert too_large.json()["code"] == "COMMON_VALIDATION_ERROR"
    zero_page = await client.get("/internal/admin/runs", headers=headers, params={"page": 0})
    assert zero_page.status_code == 422
    assert zero_page.json()["code"] == "COMMON_VALIDATION_ERROR"

    bad_status = await client.get(
        "/internal/admin/runs", headers=headers, params={"status": "NOT_A_RUN_STATUS"}
    )
    assert bad_status.status_code == 422
    assert bad_status.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_s03_admin_run_list_filters_and_paginates(
    client: AsyncClient, tenant: TenantContext
) -> None:
    seed = await _seed_list_facts(tenant)
    oldest, middle, newest = seed.run_ids
    headers = _admin_headers(tenant.tenant_id)

    data = _assert_list_envelope(await _list_runs(client, headers))
    _assert_default_list_page(data, seed)
    await _assert_status_filter_narrows(client, headers, middle=middle)
    await _assert_skill_filter_narrows(client, headers, middle=middle, skill_id=seed.skill_id)
    await _assert_user_filter_keeps_total(client, headers, user_id=tenant.platform_user_id)
    await _assert_time_window_filter_narrows(client, headers, middle=middle, newest=newest)
    await _assert_pagination_splits_pages(client, headers, oldest=oldest)
    await _assert_invalid_query_params_rejected(client, headers)


async def _seed_detail_authorities(tenant: TenantContext) -> None:
    """详情用例的 control.* 权威行：一个 Agent + 一个平台用户。"""
    async with get_session_factory()() as session:
        await _seed_authoritative_names(
            session, tenant, agent_id=tenant.agent_id, agent_name="策略检查助手"
        )
        await _seed_platform_user(
            session, tenant, user_id=tenant.platform_user_id, user_name="张三"
        )
        await session.commit()


async def _seed_detail_run(
    tenant: TenantContext, *, skill_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    """失败态 Run（含 Skill 快照），返回 (conversation_id, run_id)。"""
    return await _seed_run(
        tenant,
        agent_id=tenant.agent_id,
        user_id=tenant.platform_user_id,
        status="FAILED",
        start_time=datetime.now(UTC) - timedelta(minutes=5),
        end_time=datetime.now(UTC),
        error_code="MODEL_UNAVAILABLE",
        input_text=RAW_INPUT,
        skill_id=skill_id,
    )


async def _seed_canonical_timeline(
    tenant: TenantContext, *, conversation_id: uuid.UUID, run_id: uuid.UUID
) -> None:
    """Canonical Timeline：经真实 EventWriter 落库（append-only，seq 由 conversation.last_seq 分配）"""
    async with get_session_factory()() as session:
        writer = EventWriter(session)
        await writer.append(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            event_type="USER_MESSAGE",
            payload={"id": "msg-1", "type": "text", "text": "查一下设备"},
        )
        await writer.append(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            event_type="ASSISTANT_MESSAGE",
            payload={"text": "已查询"},
        )
        await session.commit()


async def _seed_tool_audit(writer: RuntimeAuditWriter, *, now: datetime) -> None:
    await writer.record_tool_call(
        tool_call_id="call-1",
        tool_name="execute_skill",
        tool_kind="SKILL",
        prepared_args_hash="sha256:seed",
        args_preview_json={"query": "devices", "api_key": SECRET_ARGS},
        status="OK",
        start_time=now,
        end_time=now,
        latency_ms=812,
    )


async def _seed_egress_audit(writer: RuntimeAuditWriter) -> None:
    await writer.record_egress(
        target_type="PLATFORM_SERVICE",
        target="customer-service-mgr",
        policy_decision="ALLOW",
        operation="get_customer",
        method="POST",
        status_code=200,
        result_status="OK",
        latency_ms=233,
    )


async def _seed_model_invocation_audit(writer: RuntimeAuditWriter) -> None:
    await writer.record_model_invocation(
        provider="openai-compatible",
        model="qwen3-235b-a22b",
        attempt=1,
        retry_reason=None,
        input_tokens=1832,
        output_tokens=226,
        latency_ms=1401,
        status="FAILED",
        error_code="MODEL_UNAVAILABLE",
    )


async def _seed_audit_facts(
    tenant: TenantContext, *, conversation_id: uuid.UUID, run_id: uuid.UUID
) -> None:
    """三类审计经真实脱敏写入 port 落库（args_preview 含像 Secret 的值，写前已递归遮蔽）。"""
    writer = RuntimeAuditWriter(
        tenant_id=tenant.tenant_id,
        run_id=run_id,
        task_id=None,
        conversation_id=conversation_id,
        user_id=tenant.platform_user_id,
        session_factory=get_session_factory,
    )
    now = datetime.now(UTC)
    await _seed_tool_audit(writer, now=now)
    await _seed_egress_audit(writer)
    await _seed_model_invocation_audit(writer)


async def _seed_artifact(
    tenant: TenantContext,
    *,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    artifact_preview: str,
) -> None:
    async with get_session_factory()() as session:
        session.add(
            Artifact(
                tenant_id=tenant.tenant_id,
                run_id=run_id,
                task_id=None,
                conversation_id=conversation_id,
                artifact_type="TOOL_RESULT",
                storage_key=f"tools/{tenant.tenant_id}/{run_id}/result.bin",
                media_type="text/plain",
                size=20480,
                checksum="sha256:" + "b" * 64,
                preview_text=artifact_preview,
                metadata_json={"tool_call_id": "call-1"},
            )
        )
        await session.commit()


def _assert_detail_run_section(
    data: dict[str, Any],
    tenant: TenantContext,
    *,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> None:
    run_section = data["run"]
    assert set(run_section) == RUN_FIELDS
    assert run_section["run_id"] == str(run_id)
    assert run_section["conversation_id"] == str(conversation_id)
    assert run_section["agent_id"] == str(tenant.agent_id)
    assert run_section["agent_name"] == "策略检查助手"
    assert run_section["user_id"] == str(tenant.platform_user_id)
    assert run_section["user_name"] == "张三"
    assert run_section["status"] == "FAILED"
    assert run_section["cancel_requested"] is False
    assert run_section["error_code"] == "MODEL_UNAVAILABLE"
    assert datetime.fromisoformat(run_section["start_time"]).tzinfo is not None
    assert datetime.fromisoformat(run_section["end_time"]).tzinfo is not None


def _assert_detail_snapshot_section(data: dict[str, Any]) -> None:
    snapshot = data["snapshot"]
    assert set(snapshot) == SNAPSHOT_FIELDS
    assert snapshot["schema_version"] == 1
    assert snapshot["agent_revision"] == 3
    assert snapshot["model_revision"] == 2
    assert snapshot["prompt_template_version"] == "1"
    assert snapshot["content_hash"] == "sha256:" + "a" * 64
    assert uuid.UUID(snapshot["snapshot_id"])


def _assert_detail_timeline_section(data: dict[str, Any]) -> None:
    assert [row["event_type"] for row in data["timeline"]] == ["USER_MESSAGE", "ASSISTANT_MESSAGE"]
    for row in data["timeline"]:
        assert set(row) == TIMELINE_FIELDS
    assert data["timeline"][0]["seq"] == 1
    assert data["timeline"][0]["payload"]["text"] == "查一下设备"


def _assert_detail_tool_audit_section(data: dict[str, Any]) -> None:
    tool_audit = data["tool_audits"][0]
    assert set(tool_audit) == TOOL_AUDIT_FIELDS
    assert tool_audit["tool_name"] == "execute_skill"
    assert tool_audit["tool_kind"] == "SKILL"
    assert tool_audit["status"] == "OK"
    assert tool_audit["latency_ms"] == 812
    assert tool_audit["error_code"] is None


def _assert_detail_egress_audit_section(data: dict[str, Any]) -> None:
    egress_audit = data["egress_audits"][0]
    assert set(egress_audit) == EGRESS_AUDIT_FIELDS
    assert egress_audit["target_type"] == "PLATFORM_SERVICE"
    assert egress_audit["target"] == "customer-service-mgr"
    assert egress_audit["operation"] == "get_customer"
    assert egress_audit["policy_decision"] == "ALLOW"
    assert egress_audit["result_status"] == "OK"
    assert egress_audit["latency_ms"] == 233


def _assert_detail_model_invocation_section(data: dict[str, Any]) -> None:
    model_invocation = data["model_invocations"][0]
    assert set(model_invocation) == MODEL_AUDIT_FIELDS
    assert model_invocation["provider"] == "openai-compatible"
    assert model_invocation["model"] == "qwen3-235b-a22b"
    assert model_invocation["attempt"] == 1
    assert model_invocation["input_tokens"] == 1832
    assert model_invocation["output_tokens"] == 226
    assert model_invocation["latency_ms"] == 1401


def _assert_detail_artifact_section(data: dict[str, Any], *, artifact_preview: str) -> None:
    artifact = data["artifacts"][0]
    assert set(artifact) == ARTIFACT_FIELDS
    assert artifact["artifact_type"] == "TOOL_RESULT"
    assert artifact["media_type"] == "text/plain"
    assert artifact["size"] == 20480
    assert artifact["preview"] == artifact_preview
    assert datetime.fromisoformat(artifact["create_time"]).tzinfo is not None


def _assert_response_hides_secrets(serialized: str, data: dict[str, Any]) -> None:
    # 响应（含快照 model_json / error_message / input_text 等未投影字段）不得出现 Secret 或原始 Prompt
    for marker in LEAK_MARKERS:
        assert marker not in serialized, f"响应泄露敏感内容：{marker}"
    assert "input_text" not in json.dumps(data)
    assert "error_message" not in serialized


async def _assert_redaction_applied_in_db(run_id: uuid.UUID) -> None:
    """真实写入 port 的脱敏在库内生效（负断言的可信前提）。"""
    async with get_session_factory()() as session:
        stored = await session.execute(
            sa.select(ToolCallAudit.args_preview_json).where(ToolCallAudit.run_id == run_id)
        )
        preview_json = stored.scalar_one()
    assert preview_json["api_key"] == "<redacted>"
    assert SECRET_ARGS not in json.dumps(preview_json)


async def test_s03_admin_run_detail_returns_full_contract_without_secret(
    client: AsyncClient, tenant: TenantContext
) -> None:
    await _seed_detail_authorities(tenant)
    skill_id = uuid.uuid4()
    conversation_id, run_id = await _seed_detail_run(tenant, skill_id=skill_id)
    await _seed_canonical_timeline(tenant, conversation_id=conversation_id, run_id=run_id)
    await _seed_audit_facts(tenant, conversation_id=conversation_id, run_id=run_id)
    artifact_preview = "device list preview"
    await _seed_artifact(
        tenant, conversation_id=conversation_id, run_id=run_id, artifact_preview=artifact_preview
    )

    response = await client.get(
        f"/internal/admin/runs/{run_id}", headers=_admin_headers(tenant.tenant_id)
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert set(data) == DETAIL_SECTIONS

    _assert_detail_run_section(data, tenant, run_id=run_id, conversation_id=conversation_id)
    _assert_detail_snapshot_section(data)
    _assert_detail_timeline_section(data)
    _assert_detail_tool_audit_section(data)
    _assert_detail_egress_audit_section(data)
    _assert_detail_model_invocation_section(data)
    _assert_detail_artifact_section(data, artifact_preview=artifact_preview)
    _assert_response_hides_secrets(response.text, data)
    await _assert_redaction_applied_in_db(run_id)


async def test_s03_admin_run_detail_without_snapshot_or_artifacts_is_empty(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """快照/审计/Artifact 缺失时按 null/空数组降级，不整体失败。"""
    async with get_session_factory()() as session:
        await _seed_platform_user(
            session, tenant, user_id=tenant.platform_user_id, user_name="张三"
        )
        await session.commit()
    _, run_id = await _seed_run(
        tenant,
        agent_id=tenant.agent_id,
        user_id=tenant.platform_user_id,
        status="CREATED",
        start_time=None,
        end_time=None,
    )

    response = await client.get(
        f"/internal/admin/runs/{run_id}", headers=_admin_headers(tenant.tenant_id)
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert set(data) == DETAIL_SECTIONS
    assert data["snapshot"] is None
    assert data["timeline"] == []
    assert data["tool_audits"] == []
    assert data["egress_audits"] == []
    assert data["model_invocations"] == []
    assert data["artifacts"] == []
    # control.* 无权威行时名称回落 null，不报错
    assert data["run"]["agent_name"] is None
    assert data["run"]["user_name"] == "张三"
    assert data["run"]["start_time"] is None
    assert data["run"]["end_time"] is None


async def test_s03_admin_run_detail_unknown_run_is_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    response = await client.get(
        f"/internal/admin/runs/{uuid.uuid4()}", headers=_admin_headers(tenant.tenant_id)
    )
    assert response.status_code == 404
    assert response.json()["code"] == "COMMON_NOT_FOUND"

    cross_tenant = await _seed_run(
        tenant,
        agent_id=tenant.agent_id,
        user_id=tenant.platform_user_id,
        status="COMPLETED",
        start_time=datetime.now(UTC),
        end_time=datetime.now(UTC),
    )
    hidden = await client.get(
        f"/internal/admin/runs/{cross_tenant[1]}",
        headers=_admin_headers(f"test-other-{uuid.uuid4()}"),
    )
    assert hidden.status_code == 404
    assert hidden.json()["code"] == "COMMON_NOT_FOUND"


async def test_s03_admin_run_endpoints_require_internal_service_identity(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """内部凭据端点：缺失/错误服务身份一律拒绝（api-kit `require_internal_service` 口径）。"""
    run_id = uuid.uuid4()
    anonymous = await client.get("/internal/admin/runs", headers={TENANT_HEADER: tenant.tenant_id})
    assert anonymous.status_code == 403
    assert anonymous.json()["code"] == "FORBIDDEN"

    wrong = await client.get(
        f"/internal/admin/runs/{run_id}",
        headers={INTERNAL_HEADER: "not-the-token", TENANT_HEADER: tenant.tenant_id},
    )
    assert wrong.status_code == 403
    assert wrong.json()["code"] == "FORBIDDEN"
