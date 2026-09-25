"""[S-01][E-03] 审计聚合列表 API-01（真实 Console HTTP + 真实 PostgreSQL 四张审计表投影）。

S-01 边界：Browser→Console 聚合查询 HTTP→四张审计表（PostgreSQL）。
E-03 边界：Console 查询参数校验→真实 PostgreSQL。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
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

# 四张表的审计行 create_time：按分钟错开，保证 ORDER BY occurred_at DESC 结果唯一可断言
OCCURRED_OFFSETS_MINUTES = {"CONFIG": 3, "TOOL": 2, "EGRESS": 1, "MODEL": 0}

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
            'audit seed', :trace_id, now(), now(), false)
    """
)

_INSERT_CONFIG_AUDIT = text(
    """
    INSERT INTO control.config_audit_log
        (id, tenant_id, actor_user_id, resource_type, resource_id, action,
         before_json, after_json, trace_id, create_time)
    VALUES (:audit_id, :tenant_id, :actor_user_id, 'AGENT', :agent_id, 'UPDATE',
            NULL, NULL, :trace_id, :occurred_at)
    """
)

_INSERT_TOOL_AUDIT = text(
    """
    INSERT INTO runtime.tool_call_audit
        (id, tenant_id, run_id, task_id, conversation_id, tool_call_id, tool_name,
         tool_kind, prepared_args_hash, args_preview_json, status, start_time,
         end_time, latency_ms, create_time)
    VALUES (:audit_id, :tenant_id, :run_id, NULL, :conversation_id, 'call-1',
            'execute_skill', 'SKILL', 'sha256:seed', '{}'::jsonb, 'OK', :occurred_at,
            :occurred_at, 812, :occurred_at)
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
class AuditSeed:
    """一个租户内同一条 trace 的四类审计事实（真实 SQL 落库）。"""

    trace_id: str
    account_id: uuid.UUID
    account_name: str
    agent_id: uuid.UUID
    agent_name: str
    user_id: uuid.UUID
    user_name: str
    run_id: uuid.UUID
    egress_target: str


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


async def _seed_agent(session: AsyncSession, tenant: TenantContext, seed: AuditSeed) -> None:
    await _insert(
        session,
        _INSERT_AGENT,
        {
            "agent_id": seed.agent_id,
            "tenant_id": tenant.tenant_id,
            "key": f"audit-agent-{seed.agent_id.hex[:8]}",
            "agent_name": seed.agent_name,
            "model_id": tenant.model_id,
        },
    )


async def _seed_platform_user(session: AsyncSession, tenant: TenantContext, seed: AuditSeed) -> None:
    await _insert(
        session,
        _INSERT_PLATFORM_USER,
        {
            "user_id": seed.user_id,
            "tenant_id": tenant.tenant_id,
            "user_code": f"audit-user-{seed.user_id.hex[:8]}",
            "user_name": seed.user_name,
        },
    )


async def _seed_conversation_and_run(
    session: AsyncSession, tenant: TenantContext, seed: AuditSeed
) -> uuid.UUID:
    """落库 conversation 与 run_record（依赖前两行的 agent/user 外键），返回会话 id。"""
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


def _occurred_times() -> dict[str, datetime]:
    base = datetime.now(UTC)
    return {
        audit_type: base - timedelta(minutes=minutes)
        for audit_type, minutes in OCCURRED_OFFSETS_MINUTES.items()
    }


async def _seed_config_audit(
    session: AsyncSession, tenant: TenantContext, seed: AuditSeed, occurred_at: datetime
) -> None:
    await _insert(
        session,
        _INSERT_CONFIG_AUDIT,
        {
            "audit_id": uuid.uuid4(),
            "tenant_id": tenant.tenant_id,
            "actor_user_id": seed.account_id,
            "agent_id": seed.agent_id,
            "trace_id": seed.trace_id,
            "occurred_at": occurred_at,
        },
    )


async def _seed_runtime_audits(
    session: AsyncSession,
    tenant: TenantContext,
    seed: AuditSeed,
    conversation_id: uuid.UUID,
    times: dict[str, datetime],
) -> None:
    await _insert(
        session,
        _INSERT_TOOL_AUDIT,
        {
            "audit_id": uuid.uuid4(),
            "tenant_id": tenant.tenant_id,
            "run_id": seed.run_id,
            "conversation_id": conversation_id,
            "occurred_at": times["TOOL"],
        },
    )
    await _insert(
        session,
        _INSERT_EGRESS_AUDIT,
        {
            "audit_id": uuid.uuid4(),
            "tenant_id": tenant.tenant_id,
            "run_id": seed.run_id,
            "user_id": seed.user_id,
            "target": seed.egress_target,
            "occurred_at": times["EGRESS"],
        },
    )
    await _insert(
        session,
        _INSERT_MODEL_AUDIT,
        {
            "audit_id": uuid.uuid4(),
            "tenant_id": tenant.tenant_id,
            "run_id": seed.run_id,
            "occurred_at": times["MODEL"],
        },
    )


async def _seed(session: AsyncSession, tenant: TenantContext, seed: AuditSeed) -> None:
    """按外键依赖顺序落前置实体，再写入同一 trace 的四类审计事实。"""
    await _seed_agent(session, tenant, seed)
    await _seed_platform_user(session, tenant, seed)
    conversation_id = await _seed_conversation_and_run(session, tenant, seed)

    times = _occurred_times()
    await _seed_config_audit(session, tenant, seed, times["CONFIG"])
    await _seed_runtime_audits(session, tenant, seed, conversation_id, times)


@pytest.fixture
async def audit_seed(client: AsyncClient, tenant: TenantContext) -> AsyncIterator[AuditSeed]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        account_id, account_name = await _registered_account(session, tenant.tenant_id)
    seed = AuditSeed(
        trace_id=f"trace-{uuid.uuid4().hex}",
        account_id=account_id,
        account_name=account_name,
        agent_id=uuid.uuid4(),
        agent_name="Audit Agent",
        user_id=uuid.uuid4(),
        user_name="Audit User",
        run_id=uuid.uuid4(),
        egress_target="customer-service-mgr",
    )
    async with session_factory() as session:
        await _seed(session, tenant, seed)
        await session.commit()
    try:
        yield seed
    finally:
        async with session_factory() as session:
            for statement in _CLEANUP_STATEMENTS:
                await session.execute(text(statement), {"tenant_id": tenant.tenant_id})
            await session.commit()


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


def _by_type(items: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(item["audit_type"]): item for item in items}


async def _fetch_trace_audits(
    client: AsyncClient, tenant: TenantContext, seed: AuditSeed
) -> dict[str, Any]:
    """[S-01] 按同一 trace 拉取聚合列表首页，返回已校验 200 的响应体。"""
    response = await client.get(
        "/api/v1/audits",
        params={"trace_id": seed.trace_id, "page": 1, "page_size": 20},
        headers=_headers(tenant),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _assert_audit_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    assert envelope["code"] == "0"
    for field in ("code", "msg", "data", "trace_id", "request_id", "timestamp"):
        assert field in envelope, f"封套缺字段 {field}"
    page = envelope["data"]
    for field in ("items", "page", "page_size", "total"):
        assert field in page, f"分页封套缺字段 {field}"
    assert page["page"] == 1
    assert page["page_size"] == 20
    assert page["total"] == 4
    return page


def _assert_unified_items(items: list[dict[str, object]], seed: AuditSeed) -> None:
    assert len(items) == 4
    # 四表 UNION ALL + ORDER BY occurred_at DESC
    assert [item["audit_type"] for item in items] == ["MODEL", "EGRESS", "TOOL", "CONFIG"]
    occurred = [str(item["occurred_at"]) for item in items]
    assert occurred == sorted(occurred, reverse=True)

    for item in items:
        for field in UNIFIED_FIELDS:
            assert field in item, f"{item['audit_type']} 缺统一字段 {field}"
        assert item["trace_id"] == seed.trace_id
        assert DATE_TIME_PATTERN.match(str(item["occurred_at"])), item["occurred_at"]
        if item["started_at"] is not None:
            assert DATE_TIME_PATTERN.match(str(item["started_at"])), item["started_at"]
        if item["finished_at"] is not None:
            assert DATE_TIME_PATTERN.match(str(item["finished_at"])), item["finished_at"]
        assert "secret" not in item
        assert "args_preview_json" not in item


def _assert_config_row(row: dict[str, object], seed: AuditSeed) -> None:
    assert row["audit_id"]
    assert row["resource_type"] == "AGENT"
    assert row["resource_id"] == str(seed.agent_id)
    assert row["actor_user_id"] == str(seed.account_id)
    assert row["actor_name"] == seed.account_name
    assert row["agent_id"] is None
    assert row["agent_name"] is None
    assert row["action"] == "UPDATE"
    assert row["result_status"] == "SUCCESS"
    assert row["target"] == f"AGENT/{seed.agent_id}"
    assert row["started_at"] == row["occurred_at"]
    assert row["finished_at"] is None
    assert row["latency_ms"] is None


def _assert_tool_row(row: dict[str, object], seed: AuditSeed) -> None:
    assert row["resource_type"] == "TOOL"
    assert row["resource_id"] == str(seed.run_id)
    assert row["actor_user_id"] == str(seed.user_id)
    assert row["actor_name"] == seed.user_name
    assert row["agent_id"] == str(seed.agent_id)
    assert row["agent_name"] == seed.agent_name
    assert row["action"] == "execute_skill"
    assert row["result_status"] == "SUCCESS"  # tool_call_audit.status = OK
    assert row["target"] == "execute_skill"
    assert row["started_at"] is not None
    assert row["finished_at"] is not None
    assert row["latency_ms"] == 812


def _assert_egress_row(row: dict[str, object], seed: AuditSeed) -> None:
    assert row["resource_type"] == "MCP"
    # platform_id 为空时回落到 target（设计 §3.3 投影规则）
    assert row["resource_id"] == seed.egress_target
    assert row["actor_user_id"] == str(seed.user_id)
    assert row["actor_name"] == seed.user_name
    assert row["agent_id"] == str(seed.agent_id)
    assert row["agent_name"] == seed.agent_name
    assert row["action"] == "mcp.tool"
    assert row["result_status"] == "DENIED"  # 非 OK/ERROR 原样透出
    assert row["finished_at"] is None
    assert row["latency_ms"] == 12


def _assert_model_row(row: dict[str, object], seed: AuditSeed) -> None:
    assert row["resource_type"] == "MODEL"
    assert row["resource_id"] == str(seed.run_id)
    assert row["actor_name"] == seed.user_name
    assert row["agent_name"] == seed.agent_name
    assert row["action"] == "openai-compatible/qwen3-235b-a22b"
    assert row["result_status"] == "FAILED"  # status = ERROR 归一
    assert row["target"] == "openai-compatible/qwen3-235b-a22b"
    assert row["finished_at"] is None
    assert row["latency_ms"] == 1401


async def test_s01_audit_list_projects_four_tables_with_unified_fields(
    client: AsyncClient, tenant: TenantContext, audit_seed: AuditSeed
) -> None:
    """[S-01] 同一 trace 的四类审计经聚合投影后字段归一、可排序分页。"""
    envelope = await _fetch_trace_audits(client, tenant, audit_seed)
    page = _assert_audit_envelope(envelope)
    items: list[dict[str, object]] = page["items"]
    _assert_unified_items(items, audit_seed)

    rows = _by_type(items)
    _assert_config_row(rows["CONFIG"], audit_seed)
    _assert_tool_row(rows["TOOL"], audit_seed)
    _assert_egress_row(rows["EGRESS"], audit_seed)
    _assert_model_row(rows["MODEL"], audit_seed)


async def test_e03_invalid_filters_are_rejected_without_full_dump(
    client: AsyncClient, tenant: TenantContext, audit_seed: AuditSeed
) -> None:
    """[E-03] 非法枚举与非法时间区间 → COMMON_VALIDATION_ERROR，且不返回未过滤全量。"""
    invalid_status = await client.get(
        "/api/v1/audits",
        params={"result_status": "NOT_A_REGISTERED_STATUS"},
        headers=_headers(tenant),
    )
    assert invalid_status.status_code == 422, invalid_status.text
    body = invalid_status.json()
    assert body["code"] == "COMMON_VALIDATION_ERROR"
    assert "items" not in (body.get("data") or {})

    start = datetime.now(UTC) + timedelta(hours=1)
    end = datetime.now(UTC) - timedelta(hours=1)
    reversed_range = await client.get(
        "/api/v1/audits",
        params={"start_time": start.isoformat(), "end_time": end.isoformat()},
        headers=_headers(tenant),
    )
    assert reversed_range.status_code == 422, reversed_range.text
    body = reversed_range.json()
    assert body["code"] == "COMMON_VALIDATION_ERROR"
    assert "items" not in (body.get("data") or {})

    malformed_range = await client.get(
        "/api/v1/audits",
        params={"start_time": "not-a-timestamp", "end_time": end.isoformat()},
        headers=_headers(tenant),
    )
    assert malformed_range.status_code == 422, malformed_range.text
    assert malformed_range.json()["code"] == "COMMON_VALIDATION_ERROR"

    # 反证：同样的种子数据在合法查询下确实有 4 条，说明上面的拒绝不是"本来就查不到"
    valid = await client.get(
        "/api/v1/audits", params={"page": 1, "page_size": 100}, headers=_headers(tenant)
    )
    assert valid.status_code == 200, valid.text
    assert valid.json()["data"]["total"] == 4


async def test_ru02_page_bounds_are_enforced(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[RULE-02] 分页边界：page_size > 100 与 page < 1 均被拒绝。"""
    too_large = await client.get(
        "/api/v1/audits", params={"page_size": 101}, headers=_headers(tenant)
    )
    assert too_large.status_code == 422
    assert too_large.json()["code"] == "COMMON_VALIDATION_ERROR"

    bad_page = await client.get("/api/v1/audits", params={"page": 0}, headers=_headers(tenant))
    assert bad_page.status_code == 422
    assert bad_page.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_ru02_type_and_status_filters_narrow_the_projection(
    client: AsyncClient, tenant: TenantContext, audit_seed: AuditSeed
) -> None:
    """[RULE-02] audit_type / result_status 过滤命中归一后的投影列。"""
    only_model = await client.get(
        "/api/v1/audits", params={"audit_type": "MODEL"}, headers=_headers(tenant)
    )
    assert only_model.status_code == 200, only_model.text
    model_page = only_model.json()["data"]
    assert model_page["total"] == 1
    assert model_page["items"][0]["audit_type"] == "MODEL"

    failed = await client.get(
        "/api/v1/audits", params={"result_status": "FAILED"}, headers=_headers(tenant)
    )
    assert failed.status_code == 200, failed.text
    failed_page = failed.json()["data"]
    assert failed_page["total"] == 1
    assert failed_page["items"][0]["audit_type"] == "MODEL"

    unknown_type = await client.get(
        "/api/v1/audits", params={"audit_type": "BOGUS"}, headers=_headers(tenant)
    )
    assert unknown_type.status_code == 422, unknown_type.text
    assert unknown_type.json()["code"] == "COMMON_VALIDATION_ERROR"
