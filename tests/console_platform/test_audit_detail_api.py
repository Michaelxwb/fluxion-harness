"""[E-01][API-02] 审计详情 API-02（真实 Console HTTP + 真实 PostgreSQL 四张审计表）。

E-01 边界：Console 详情 HTTP → 按 `audit_type` 定位来源表 → 真实 PostgreSQL；
关联 Run/Task 已归档/不可读时审计自身仍可展示，`related` 置空并标记 missing，不伪造关联。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncSession

from console_platform.conftest import TenantContext

DATE_TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

UNIFIED_FIELDS = (
    "audit_id",
    "audit_type",
    "resource_type",
    "resource_id",
    "actor_user_id",
    "actor_name",
    "agent_id",
    "agent_name",
    "action",
    "result_status",
    "trace_id",
    "target",
    "occurred_at",
    "started_at",
    "finished_at",
    "latency_ms",
)

CONFIG_BEFORE: dict[str, Any] = {"name": "Audit Agent", "enabled": True}
CONFIG_AFTER: dict[str, Any] = {"name": "Audit Agent Renamed", "enabled": False}
TOOL_ARGS_PREVIEW: dict[str, Any] = {"skill_key": "summarize", "input_chars": 42}

# 各审计类型的来源表（UUID 跨 4 表不互通，详情必须显式指定；表名取自本字面量，不入参）
AUDIT_TABLES = {
    "CONFIG": "control.config_audit_log",
    "TOOL": "runtime.tool_call_audit",
    "EGRESS": "runtime.egress_audit",
    "MODEL": "runtime.model_invocation_audit",
}

_INSERT_AGENT = text(
    """
    INSERT INTO control.agent_definition
        (id, tenant_id, key, name, description, instructions, model_id,
         runtime_config_json, revision, enabled)
    VALUES (:agent_id, :tenant_id, :key, :agent_name, NULL, '', :model_id,
            '{}'::jsonb, 1, true)
    """
)

_INSERT_PLATFORM_USER = text(
    """
    INSERT INTO control.platform_user (id, tenant_id, user_code, display_name, status)
    VALUES (:user_id, :tenant_id, :user_code, :user_name, 'ACTIVE')
    """
)

_INSERT_CONVERSATION = text(
    """
    INSERT INTO runtime.conversation (id, tenant_id, user_id, agent_id)
    VALUES (:conversation_id, :tenant_id, :user_id, :agent_id)
    """
)

_INSERT_RUN = text(
    """
    INSERT INTO runtime.run_record
        (id, tenant_id, conversation_id, user_id, agent_id, status, input_text,
         trace_id, start_time, end_time, cancel_requested)
    VALUES (:run_id, :tenant_id, :conversation_id, :user_id, :agent_id, 'SUCCEEDED',
            'audit detail seed', :trace_id, now(), now(), false)
    """
)

_INSERT_CONFIG_AUDIT = text(
    """
    INSERT INTO control.config_audit_log
        (id, tenant_id, actor_user_id, resource_type, resource_id, action,
         before_json, after_json, trace_id, source_ip, create_time)
    VALUES (:audit_id, :tenant_id, :actor_user_id, 'AGENT', :agent_id, 'UPDATE',
            CAST(:before_json AS jsonb), CAST(:after_json AS jsonb), :trace_id,
            '203.0.113.7', :occurred_at)
    """
)

_INSERT_TOOL_AUDIT = text(
    """
    INSERT INTO runtime.tool_call_audit
        (id, tenant_id, run_id, task_id, conversation_id, tool_call_id, tool_name,
         tool_kind, prepared_args_hash, args_preview_json, status, start_time,
         end_time, latency_ms, create_time)
    VALUES (:audit_id, :tenant_id, :run_id, NULL, :conversation_id, :tool_call_id,
            'execute_skill', 'SKILL', 'sha256:seed', CAST(:args_preview_json AS jsonb),
            'OK', :occurred_at, :occurred_at, 812, :occurred_at)
    """
)

_INSERT_EGRESS_AUDIT = text(
    """
    INSERT INTO runtime.egress_audit
        (id, tenant_id, run_id, task_id, user_id, platform_id, target_type, target,
         operation, method, policy_decision, status_code, result_status, latency_ms,
         create_time)
    VALUES (:audit_id, :tenant_id, :run_id, NULL, :user_id, NULL, 'MCP', :target,
            'mcp.tool', 'POST', 'DENY', NULL, 'DENIED', 12, :occurred_at)
    """
)

_INSERT_MODEL_AUDIT = text(
    """
    INSERT INTO runtime.model_invocation_audit
        (id, tenant_id, run_id, task_id, provider, model, attempt, retry_reason,
         input_tokens, output_tokens, latency_ms, status, create_time)
    VALUES (:audit_id, :tenant_id, :run_id, NULL, 'openai-compatible',
            'qwen3-235b-a22b', 1, 'request_rejected', 1832, 226, 1401, 'ERROR',
            :occurred_at)
    """
)

_CLEANUP_STATEMENTS = (
    "DELETE FROM runtime.tool_call_audit WHERE tenant_id = :tenant_id",
    "DELETE FROM runtime.egress_audit WHERE tenant_id = :tenant_id",
    "DELETE FROM runtime.model_invocation_audit WHERE tenant_id = :tenant_id",
    "DELETE FROM runtime.run_record WHERE tenant_id = :tenant_id",
    "DELETE FROM runtime.conversation WHERE tenant_id = :tenant_id",
    "DELETE FROM control.config_audit_log WHERE tenant_id = :tenant_id",
    "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id",
    "DELETE FROM control.agent_definition WHERE tenant_id = :tenant_id",
)


@dataclass(frozen=True)
class AuditDetailSeed:
    """一个租户内同一条 trace 的四类审计事实（真实 SQL 落库）与各表 `audit_id`。"""

    trace_id: str
    account_id: uuid.UUID
    account_name: str
    agent_id: uuid.UUID
    agent_name: str
    user_id: uuid.UUID
    user_name: str
    run_id: uuid.UUID
    egress_target: str
    audit_ids: dict[str, uuid.UUID]


async def _registered_account(session: AsyncSession, tenant_id: str) -> tuple[uuid.UUID, str]:
    row = (
        await session.execute(
            text(
                "SELECT id, display_name FROM control.console_account "
                "WHERE tenant_id = :tenant_id"
            ),
            {"tenant_id": tenant_id},
        )
    ).one()
    return row.id, row.display_name


async def _insert(session: AsyncSession, statement: TextClause, params: dict[str, object]) -> None:
    await session.execute(statement, params)


async def _seed_agent(session: AsyncSession, tenant: TenantContext, seed: AuditDetailSeed) -> None:
    await _insert(
        session,
        _INSERT_AGENT,
        {
            "agent_id": seed.agent_id,
            "tenant_id": tenant.tenant_id,
            "key": f"audit-detail-agent-{seed.agent_id.hex[:8]}",
            "agent_name": seed.agent_name,
            "model_id": tenant.model_id,
        },
    )


async def _seed_platform_user(
    session: AsyncSession, tenant: TenantContext, seed: AuditDetailSeed
) -> None:
    await _insert(
        session,
        _INSERT_PLATFORM_USER,
        {
            "user_id": seed.user_id,
            "tenant_id": tenant.tenant_id,
            "user_code": f"audit-detail-user-{seed.user_id.hex[:8]}",
            "user_name": seed.user_name,
        },
    )


async def _seed_conversation_and_run(
    session: AsyncSession, tenant: TenantContext, seed: AuditDetailSeed
) -> uuid.UUID:
    """落库 conversation 与 run_record（依赖前两行的 agent/user），返回会话 id。"""
    conversation_id = uuid.uuid4()
    await _insert(
        session,
        _INSERT_CONVERSATION,
        {
            "conversation_id": conversation_id,
            "tenant_id": tenant.tenant_id,
            "user_id": seed.user_id,
            "agent_id": seed.agent_id,
        },
    )
    await _insert(
        session,
        _INSERT_RUN,
        {
            "run_id": seed.run_id,
            "tenant_id": tenant.tenant_id,
            "conversation_id": conversation_id,
            "user_id": seed.user_id,
            "agent_id": seed.agent_id,
            "trace_id": seed.trace_id,
        },
    )
    return conversation_id


async def _seed_config_audit(
    session: AsyncSession, tenant: TenantContext, seed: AuditDetailSeed, occurred_at: datetime
) -> uuid.UUID:
    audit_id = uuid.uuid4()
    await _insert(
        session,
        _INSERT_CONFIG_AUDIT,
        {
            "audit_id": audit_id,
            "tenant_id": tenant.tenant_id,
            "actor_user_id": seed.account_id,
            "agent_id": seed.agent_id,
            "before_json": json.dumps(CONFIG_BEFORE),
            "after_json": json.dumps(CONFIG_AFTER),
            "trace_id": seed.trace_id,
            "occurred_at": occurred_at,
        },
    )
    return audit_id


async def _seed_runtime_audits(
    session: AsyncSession,
    tenant: TenantContext,
    seed: AuditDetailSeed,
    conversation_id: uuid.UUID,
    occurred_at: datetime,
) -> dict[str, uuid.UUID]:
    audit_ids: dict[str, uuid.UUID] = {}
    for audit_type, statement, params in (
        (
            "TOOL",
            _INSERT_TOOL_AUDIT,
            {
                "conversation_id": conversation_id,
                "tool_call_id": f"call-{uuid.uuid4().hex[:8]}",
                "args_preview_json": json.dumps(TOOL_ARGS_PREVIEW),
            },
        ),
        (
            "EGRESS",
            _INSERT_EGRESS_AUDIT,
            {"user_id": seed.user_id, "target": seed.egress_target},
        ),
        ("MODEL", _INSERT_MODEL_AUDIT, {}),
    ):
        audit_id = uuid.uuid4()
        audit_ids[audit_type] = audit_id
        await _insert(
            session,
            statement,
            {
                "audit_id": audit_id,
                "tenant_id": tenant.tenant_id,
                "run_id": seed.run_id,
                "occurred_at": occurred_at,
                **params,
            },
        )
    return audit_ids


async def _seed(
    session: AsyncSession, tenant: TenantContext, seed: AuditDetailSeed
) -> dict[str, uuid.UUID]:
    """按外键依赖顺序落前置实体，再写入同一 trace 的四类审计事实，返回各表 audit_id。"""
    await _seed_agent(session, tenant, seed)
    await _seed_platform_user(session, tenant, seed)
    conversation_id = await _seed_conversation_and_run(session, tenant, seed)

    base = datetime.now(UTC)
    audit_ids = {
        "CONFIG": await _seed_config_audit(session, tenant, seed, base),
        **await _seed_runtime_audits(
            session, tenant, seed, conversation_id, base - timedelta(minutes=1)
        ),
    }
    return audit_ids


@pytest.fixture
async def audit_detail_seed(
    client: AsyncClient, tenant: TenantContext
) -> AsyncIterator[AuditDetailSeed]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        account_id, account_name = await _registered_account(session, tenant.tenant_id)
    base = AuditDetailSeed(
        trace_id=f"trace-{uuid.uuid4().hex}",
        account_id=account_id,
        account_name=account_name,
        agent_id=uuid.uuid4(),
        agent_name="Audit Detail Agent",
        user_id=uuid.uuid4(),
        user_name="Audit Detail User",
        run_id=uuid.uuid4(),
        egress_target="customer-service-mgr",
        audit_ids={},
    )
    async with session_factory() as session:
        audit_ids = await _seed(session, tenant, base)
        await session.commit()
    try:
        yield replace(base, audit_ids=audit_ids)
    finally:
        async with session_factory() as session:
            for statement in _CLEANUP_STATEMENTS:
                await session.execute(text(statement), {"tenant_id": tenant.tenant_id})
            await session.commit()


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


async def _soft_delete(table: str, column: str, value: uuid.UUID) -> None:
    """把被关联/被审计的行标记为已归档（`is_deleted = true`），模拟不可读。"""
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            text(f"UPDATE {table} SET is_deleted = true WHERE {column} = :value"),
            {"value": value},
        )
        await session.commit()


async def _detail_response(
    client: AsyncClient, tenant: TenantContext, audit_id: uuid.UUID, audit_type: str
) -> tuple[int, dict[str, Any]]:
    response = await client.get(
        f"/api/v1/audits/{audit_id}",
        params={"audit_type": audit_type},
        headers=_headers(tenant),
    )
    return response.status_code, response.json()


async def _detail_data(
    client: AsyncClient, tenant: TenantContext, audit_id: uuid.UUID, audit_type: str
) -> dict[str, Any]:
    status, body = await _detail_response(client, tenant, audit_id, audit_type)
    assert status == 200, body
    assert body["code"] == "0"
    for field in ("code", "msg", "data", "trace_id", "request_id", "timestamp"):
        assert field in body, f"封套缺字段 {field}"
    data = body["data"]
    assert isinstance(data, dict)
    return data


def _assert_unified_fields(data: dict[str, Any], audit_id: uuid.UUID, audit_type: str) -> None:
    for field in UNIFIED_FIELDS:
        assert field in data, f"{audit_type} 详情缺统一字段 {field}"
    assert data["audit_id"] == str(audit_id)
    assert data["audit_type"] == audit_type  # 显式回显来源表，前端不猜表
    assert DATE_TIME_PATTERN.match(str(data["occurred_at"])), data["occurred_at"]
    assert "before_json" not in data
    assert "after_json" not in data
    assert "args_preview_json" not in data


def _assert_tool_detail(data: dict[str, Any], audit_id: uuid.UUID, seed: AuditDetailSeed) -> None:
    _assert_unified_fields(data, audit_id, "TOOL")
    assert data["resource_type"] == "TOOL"
    assert data["resource_id"] == str(seed.run_id)
    assert data["actor_user_id"] == str(seed.user_id)
    assert data["actor_name"] == seed.user_name
    assert data["agent_id"] == str(seed.agent_id)
    assert data["agent_name"] == seed.agent_name
    assert data["action"] == "execute_skill"
    assert data["result_status"] == "SUCCESS"  # tool_call_audit.status = OK
    assert data["trace_id"] == seed.trace_id
    assert data["latency_ms"] == 812
    assert data["args_preview"] == TOOL_ARGS_PREVIEW


async def _assert_error(
    client: AsyncClient,
    tenant: TenantContext,
    audit_id: uuid.UUID,
    audit_type: str | None,
    expected_status: int,
) -> None:
    """详情错误分支：缺失/非法 audit_type → 422 校验错误；命中不到 → 404 NOT_FOUND。"""
    params = {} if audit_type is None else {"audit_type": audit_type}
    response = await client.get(
        f"/api/v1/audits/{audit_id}", params=params, headers=_headers(tenant)
    )
    assert response.status_code == expected_status, response.text
    expected_code = "COMMON_NOT_FOUND" if expected_status == 404 else "COMMON_VALIDATION_ERROR"
    assert response.json()["code"] == expected_code


def _assert_config_detail(data: dict[str, Any], audit_id: uuid.UUID, seed: AuditDetailSeed) -> None:
    _assert_unified_fields(data, audit_id, "CONFIG")
    assert data["resource_type"] == "AGENT"
    assert data["resource_id"] == str(seed.agent_id)
    assert data["actor_user_id"] == str(seed.account_id)
    assert data["actor_name"] == seed.account_name
    assert data["action"] == "UPDATE"
    assert data["result_status"] == "SUCCESS"
    assert data["before"] == CONFIG_BEFORE
    assert data["after"] == CONFIG_AFTER
    assert data["related"] == {}  # config 审计不声明 Run/Task 关联
    assert data["related_missing"] is False
    assert "args_preview" not in data


def _assert_egress_detail(data: dict[str, Any], audit_id: uuid.UUID, seed: AuditDetailSeed) -> None:
    _assert_unified_fields(data, audit_id, "EGRESS")
    assert data["resource_type"] == "MCP"
    assert data["resource_id"] == seed.egress_target  # platform_id 为空时回落 target
    assert data["action"] == "mcp.tool"
    assert data["result_status"] == "DENIED"
    assert data["latency_ms"] == 12
    assert data["target_type"] == "MCP"
    assert data["method"] == "POST"
    assert data["policy_decision"] == "DENY"
    assert data["status_code"] is None
    assert data["related"] == {"run_id": str(seed.run_id)}
    assert data["related_missing"] is False


def _assert_model_detail(data: dict[str, Any], audit_id: uuid.UUID, seed: AuditDetailSeed) -> None:
    _assert_unified_fields(data, audit_id, "MODEL")
    assert data["resource_type"] == "MODEL"
    assert data["action"] == "openai-compatible/qwen3-235b-a22b"
    assert data["result_status"] == "FAILED"  # status = ERROR 归一
    assert data["provider"] == "openai-compatible"
    assert data["model"] == "qwen3-235b-a22b"
    assert data["attempt"] == 1
    assert data["retry_reason"] == "request_rejected"
    assert data["input_tokens"] == 1832
    assert data["output_tokens"] == 226
    assert data["latency_ms"] == 1401
    assert data["related"] == {"run_id": str(seed.run_id)}
    assert data["related_missing"] is False


async def test_e01_unreadable_relation_is_reported_as_missing(
    client: AsyncClient, tenant: TenantContext, audit_detail_seed: AuditDetailSeed
) -> None:
    """[E-01] 关联 Run 已归档/不可读 → 审计自身仍可展示，related 置空并标记 missing。"""
    seed = audit_detail_seed
    tool_audit_id = seed.audit_ids["TOOL"]

    readable = await _detail_data(client, tenant, tool_audit_id, "TOOL")
    _assert_tool_detail(readable, tool_audit_id, seed)
    assert readable["related"] == {"run_id": str(seed.run_id)}
    assert readable["related_missing"] is False

    await _soft_delete("runtime.run_record", "id", seed.run_id)

    degraded = await _detail_data(client, tenant, tool_audit_id, "TOOL")
    # 审计自身字段仍在（含由 run 补齐的 actor/agent 名称），只是关联降级
    _assert_tool_detail(degraded, tool_audit_id, seed)
    assert degraded["related"] == {}
    assert degraded["related_missing"] is True
    # 不伪造关联：既不回填不可读的 run_id，也不凭空造 task_id
    assert "run_id" not in degraded
    assert "task_id" not in degraded


async def test_api02_returns_detail_for_each_audit_type(
    client: AsyncClient, tenant: TenantContext, audit_detail_seed: AuditDetailSeed
) -> None:
    """[API-02] 四类审计按 (audit_id, audit_type) 单表命中，各自返回该表独有字段。"""
    seed = audit_detail_seed

    config = await _detail_data(client, tenant, seed.audit_ids["CONFIG"], "CONFIG")
    _assert_config_detail(config, seed.audit_ids["CONFIG"], seed)

    tool = await _detail_data(client, tenant, seed.audit_ids["TOOL"], "TOOL")
    _assert_tool_detail(tool, seed.audit_ids["TOOL"], seed)
    assert tool["related"] == {"run_id": str(seed.run_id)}
    assert tool["related_missing"] is False
    assert "before" not in tool
    assert "after" not in tool

    egress = await _detail_data(client, tenant, seed.audit_ids["EGRESS"], "EGRESS")
    _assert_egress_detail(egress, seed.audit_ids["EGRESS"], seed)

    model = await _detail_data(client, tenant, seed.audit_ids["MODEL"], "MODEL")
    _assert_model_detail(model, seed.audit_ids["MODEL"], seed)


async def test_api02_unknown_or_mismatched_type_is_not_found_or_invalid(
    client: AsyncClient, tenant: TenantContext, audit_detail_seed: AuditDetailSeed
) -> None:
    """[API-02] 缺失/非法 `audit_type` → 校验错误；合法类型命中不到该 id → NOT_FOUND。"""
    seed = audit_detail_seed
    tool_audit_id = seed.audit_ids["TOOL"]

    await _assert_error(client, tenant, tool_audit_id, None, 422)
    await _assert_error(client, tenant, tool_audit_id, "BOGUS", 422)
    # UUID 跨 4 表不互通：拿 CONFIG 的 id 按 TOOL 查必须落空，不猜表
    await _assert_error(client, tenant, seed.audit_ids["CONFIG"], "TOOL", 404)
    await _assert_error(client, tenant, uuid.uuid4(), "CONFIG", 404)

    # 反证：同样的 id 在合法类型下确实可读，说明上面的 404 不是"本来就查不到"
    readable = await _detail_data(client, tenant, seed.audit_ids["EGRESS"], "EGRESS")
    assert readable["audit_id"] == str(seed.audit_ids["EGRESS"])

    # 已归档（is_deleted = true）的审计行同样按不存在处理
    await _soft_delete(AUDIT_TABLES["EGRESS"], "id", seed.audit_ids["EGRESS"])
    await _assert_error(client, tenant, seed.audit_ids["EGRESS"], "EGRESS", 404)
