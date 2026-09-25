"""[B-213] API-01 / API-05 增 `agent_id` 筛选（真实 Console HTTP + 真实 PostgreSQL）。

B-213 边界：Console 列表与导出创建 HTTP → 四表 UNION ALL 投影（审计四表 + 运行表，PostgreSQL）。
语义：`agent_id` 命中**运行类**记录（该列由 run_record / task_execution 补齐）；
config 类投影恒为 NULL，即使其 `resource_id` 指向同一个 Agent 也必须被排除。
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
import shutil
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient, Response
from muad_console_platform.infrastructure.db import get_session_factory
from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncSession

from console_platform.conftest import TenantContext

LIST_PATH = "/api/v1/audits"
EXPORT_PATH = "/api/v1/audits/exports"
JOB_TABLE = "control.audit_export_job"
IDEMPOTENCY_TABLE = "control.skill_import_idempotency"
MISMATCH_MESSAGE = "相同幂等键被用于不同的请求内容，请更换幂等键后重试"
DATE_TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
POLL_ATTEMPTS = 5
POLL_INTERVAL_SEC = 0.05
TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED"})
UNKNOWN_AGENT_ID = uuid.uuid4()

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
            'agent filter seed', :trace_id, now(), now(), false)
    """
)

# config 类：`resource_id` 故意指向 A 的 Agent id，但投影的 agent_id 恒为 NULL
_INSERT_CONFIG_AUDIT = text(
    """
    INSERT INTO control.config_audit_log
        (id, tenant_id, actor_user_id, resource_type, resource_id, action,
         before_json, after_json, trace_id, create_time)
    VALUES (:audit_id, :tenant_id, :actor_user_id, 'AGENT', :resource_id, 'UPDATE',
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

_INSERT_MODEL_AUDIT = text(
    """
    INSERT INTO runtime.model_invocation_audit
        (id, tenant_id, run_id, task_id, provider, model, attempt, retry_reason,
         input_tokens, output_tokens, latency_ms, status, create_time)
    VALUES (:audit_id, :tenant_id, :run_id, NULL, 'openai-compatible',
            'qwen3-235b-a22b', 1, NULL, 1832, 226, 1401, 'OK', :occurred_at)
    """
)

_SELECT_JOB = text(
    "SELECT id, export_format, filters_json, status, row_count, artifact_ref, error_code"
    f" FROM {JOB_TABLE} WHERE tenant_id = :tenant_id ORDER BY create_time"
)

_CLEANUP_STATEMENTS = (
    f"DELETE FROM {JOB_TABLE} WHERE tenant_id = :tenant_id",
    "DELETE FROM runtime.tool_call_audit WHERE tenant_id = :tenant_id",
    "DELETE FROM runtime.model_invocation_audit WHERE tenant_id = :tenant_id",
    "DELETE FROM runtime.run_record WHERE tenant_id = :tenant_id",
    "DELETE FROM runtime.conversation WHERE tenant_id = :tenant_id",
    "DELETE FROM control.config_audit_log WHERE tenant_id = :tenant_id",
    "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id",
    "DELETE FROM control.agent_definition WHERE tenant_id = :tenant_id",
)


@dataclass(frozen=True)
class AgentFilterSeed:
    """一个租户内两个 Agent 的运行类审计 + 一条指向 A 的 config 类审计。"""

    agent_a_id: uuid.UUID
    agent_a_name: str
    agent_b_id: uuid.UUID
    agent_b_name: str
    user_id: uuid.UUID
    account_id: uuid.UUID
    audit_a_ids: tuple[uuid.UUID, uuid.UUID]  # (TOOL, MODEL)，发生时间递减
    audit_b_ids: tuple[uuid.UUID, ...]
    config_audit_id: uuid.UUID
    artifact_root: Path

    @property
    def all_audit_ids(self) -> set[str]:
        return {str(audit_id) for audit_id in (*self.audit_a_ids, *self.audit_b_ids, self.config_audit_id)}


def _headers(tenant: TenantContext, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {"X-Tenant-Id": tenant.tenant_id}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


async def _insert(session: AsyncSession, statement: TextClause, params: dict[str, Any]) -> None:
    await session.execute(statement, params)


async def _registered_account(session: AsyncSession, tenant_id: str) -> uuid.UUID:
    row = (
        await session.execute(
            text("SELECT id FROM control.console_account WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
    ).one()
    return row.id


async def _seed_agent(
    session: AsyncSession, tenant: TenantContext, agent_id: uuid.UUID, agent_name: str
) -> None:
    await _insert(
        session,
        _INSERT_AGENT,
        {
            "agent_id": agent_id,
            "tenant_id": tenant.tenant_id,
            "key": f"agent-filter-{agent_id.hex[:8]}",
            "agent_name": agent_name,
            "model_id": tenant.model_id,
        },
    )


async def _seed_run(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    seed: AgentFilterSeed,
    run_id: uuid.UUID,
    agent_id: uuid.UUID,
    trace_id: str,
) -> uuid.UUID:
    """落库 conversation + run_record（agent_id 由此进入运行类投影），返回会话 id。"""
    conversation_id = uuid.uuid4()
    await _insert(
        session,
        _INSERT_CONVERSATION,
        {
            "conversation_id": conversation_id,
            "tenant_id": tenant.tenant_id,
            "user_id": seed.user_id,
            "agent_id": agent_id,
        },
    )
    await _insert(
        session,
        _INSERT_RUN,
        {
            "run_id": run_id,
            "tenant_id": tenant.tenant_id,
            "conversation_id": conversation_id,
            "user_id": seed.user_id,
            "agent_id": agent_id,
            "trace_id": trace_id,
        },
    )
    return conversation_id


async def _insert_tool_audit(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    audit_id: uuid.UUID,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
    occurred_at: datetime,
) -> None:
    await _insert(
        session,
        _INSERT_TOOL_AUDIT,
        {
            "audit_id": audit_id,
            "tenant_id": tenant.tenant_id,
            "run_id": run_id,
            "conversation_id": conversation_id,
            "occurred_at": occurred_at,
        },
    )


async def _insert_model_audit(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    audit_id: uuid.UUID,
    run_id: uuid.UUID,
    occurred_at: datetime,
) -> None:
    await _insert(
        session,
        _INSERT_MODEL_AUDIT,
        {
            "audit_id": audit_id,
            "tenant_id": tenant.tenant_id,
            "run_id": run_id,
            "occurred_at": occurred_at,
        },
    )


async def _seed_runtime_audits(
    session: AsyncSession,
    tenant: TenantContext,
    seed: AgentFilterSeed,
    *,
    base: datetime,
) -> None:
    """A 两条运行类行（TOOL+MODEL）、B 一条；run_record.agent_id 由此进入投影。"""
    run_a_id, run_b_id = uuid.uuid4(), uuid.uuid4()
    conversation_a = await _seed_run(
        session,
        tenant,
        seed=seed,
        run_id=run_a_id,
        agent_id=seed.agent_a_id,
        trace_id=f"trace-a-{run_a_id.hex[:8]}",
    )
    conversation_b = await _seed_run(
        session,
        tenant,
        seed=seed,
        run_id=run_b_id,
        agent_id=seed.agent_b_id,
        trace_id=f"trace-b-{run_b_id.hex[:8]}",
    )
    await _insert_tool_audit(
        session,
        tenant,
        audit_id=seed.audit_a_ids[0],
        run_id=run_a_id,
        conversation_id=conversation_a,
        occurred_at=base - timedelta(minutes=2),
    )
    await _insert_model_audit(
        session,
        tenant,
        audit_id=seed.audit_a_ids[1],
        run_id=run_a_id,
        occurred_at=base - timedelta(minutes=3),
    )
    await _insert_tool_audit(
        session,
        tenant,
        audit_id=seed.audit_b_ids[0],
        run_id=run_b_id,
        conversation_id=conversation_b,
        occurred_at=base - timedelta(minutes=1),
    )


async def _seed_config_audit(
    session: AsyncSession, tenant: TenantContext, seed: AgentFilterSeed, base: datetime
) -> None:
    """config 类审计：`actor_user_id` 为登录账号，`resource_id` 指向 A 的 Agent。"""
    await _insert(
        session,
        _INSERT_CONFIG_AUDIT,
        {
            "audit_id": seed.config_audit_id,
            "tenant_id": tenant.tenant_id,
            "actor_user_id": seed.account_id,
            "resource_id": seed.agent_a_id,
            "trace_id": f"trace-config-{seed.config_audit_id.hex[:8]}",
            "occurred_at": base,
        },
    )


async def _seed(
    session: AsyncSession, tenant: TenantContext, seed: AgentFilterSeed
) -> None:
    await _seed_agent(session, tenant, seed.agent_a_id, seed.agent_a_name)
    await _seed_agent(session, tenant, seed.agent_b_id, seed.agent_b_name)
    await _insert(
        session,
        _INSERT_PLATFORM_USER,
        {
            "user_id": seed.user_id,
            "tenant_id": tenant.tenant_id,
            "user_code": f"agent-filter-user-{seed.user_id.hex[:8]}",
            "user_name": "Agent Filter User",
        },
    )
    base = datetime.now(UTC)
    await _seed_runtime_audits(session, tenant, seed, base=base)
    await _seed_config_audit(session, tenant, seed, base)


@pytest.fixture
async def agent_filter_env(
    client: AsyncClient, tenant: TenantContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AgentFilterSeed]:
    """真实审计事实 + 临时 artifact root（导出产物不落仓库 ./.data/artifacts）。"""
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    session_factory = get_session_factory()
    async with session_factory() as session:
        seed = AgentFilterSeed(
            agent_a_id=uuid.uuid4(),
            agent_a_name="Agent Filter A",
            agent_b_id=uuid.uuid4(),
            agent_b_name="Agent Filter B",
            user_id=uuid.uuid4(),
            account_id=await _registered_account(session, tenant.tenant_id),
            audit_a_ids=(uuid.uuid4(), uuid.uuid4()),
            audit_b_ids=(uuid.uuid4(),),
            config_audit_id=uuid.uuid4(),
            artifact_root=tmp_path,
        )
        await _seed(session, tenant, seed)
        await session.commit()
    try:
        yield seed
    finally:
        async with session_factory() as session:
            for statement in _CLEANUP_STATEMENTS:
                await session.execute(text(statement), {"tenant_id": tenant.tenant_id})
            await session.commit()
        shutil.rmtree(tmp_path / "exports", ignore_errors=True)


async def _list(client: AsyncClient, tenant: TenantContext, **params: Any) -> dict[str, Any]:
    """[API-01] 拉取列表页；返回已断言 200/封套的 `data`（分页封套）。"""
    response = await client.get(LIST_PATH, params=params, headers=_headers(tenant))
    assert response.status_code == 200, response.text
    assert response.json()["code"] == "0", response.text
    return response.json()["data"]


def _audit_ids(page: dict[str, Any]) -> list[str]:
    return [str(item["audit_id"]) for item in page["items"]]


def _assert_runtime_rows_of_a(page: dict[str, Any], seed: AgentFilterSeed) -> None:
    """A 的运行类行：只含 A 的两条，且都带 A 的 agent_id/agent_name。"""
    assert page["total"] == len(seed.audit_a_ids)
    assert sorted(_audit_ids(page)) == sorted(str(audit_id) for audit_id in seed.audit_a_ids)
    for item in page["items"]:
        assert item["audit_type"] in {"TOOL", "MODEL"}
        assert item["agent_id"] == str(seed.agent_a_id)
        assert item["agent_name"] == seed.agent_a_name
        assert DATE_TIME_PATTERN.match(str(item["occurred_at"])), item["occurred_at"]
    # config 类不得混入：即便它的 resource_id 指向同一个 Agent
    assert str(seed.config_audit_id) not in _audit_ids(page)


async def _post_export(
    client: AsyncClient, tenant: TenantContext, key: str, body: dict[str, Any]
) -> Response:
    return await client.post(EXPORT_PATH, json=body, headers=_headers(tenant, key))


async def _export_job_rows(tenant_id: str) -> list[dict[str, Any]]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        rows = await session.execute(_SELECT_JOB, {"tenant_id": tenant_id})
        return [dict(row) for row in rows.mappings()]


async def _count_rows(table: str, tenant_id: str) -> int:
    session_factory = get_session_factory()
    async with session_factory() as session:
        total = await session.scalar(
            text(f"SELECT count(*) FROM {table} WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
    return int(total or 0)


async def _poll_until_terminal(
    client: AsyncClient, tenant: TenantContext, export_id: str
) -> dict[str, Any]:
    """轮询状态接口直至终态：状态必须由请求链路自身推进，不手工改库。"""
    data: dict[str, Any] = {}
    for _ in range(POLL_ATTEMPTS):
        response = await client.get(f"{EXPORT_PATH}/{export_id}", headers=_headers(tenant))
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert set(data) == {
            "export_id",
            "status",
            "row_count",
            "error_code",
            "create_time",
            "update_time",
        }
        if data["status"] in TERMINAL_STATUSES:
            return data
        await asyncio.sleep(POLL_INTERVAL_SEC)
    return data


def _parse_csv(content: bytes) -> list[dict[str, str]]:
    return [dict(row) for row in csv.DictReader(io.StringIO(content.decode("utf-8")))]


async def test_b213_list_agent_filter_returns_only_that_agents_runtime_rows(
    client: AsyncClient, tenant: TenantContext, agent_filter_env: AgentFilterSeed
) -> None:
    """[B-213] `agent_id=A` 只回 A 的运行类行；B 与 config 类被排除；缺省行为不变。"""
    seed = agent_filter_env
    only_a = await _list(client, tenant, agent_id=str(seed.agent_a_id), page=1, page_size=20)
    _assert_runtime_rows_of_a(only_a, seed)

    only_b = await _list(client, tenant, agent_id=str(seed.agent_b_id), page=1, page_size=20)
    assert only_b["total"] == len(seed.audit_b_ids)
    assert _audit_ids(only_b) == [str(audit_id) for audit_id in seed.audit_b_ids]
    assert only_b["items"][0]["agent_name"] == seed.agent_b_name

    unknown = await _list(client, tenant, agent_id=str(UNKNOWN_AGENT_ID), page=1, page_size=20)
    assert unknown["total"] == 0
    assert unknown["items"] == []

    # 未传 agent_id：行为不变（A + B + config 全部返回）
    unfiltered = await _list(client, tenant, page=1, page_size=100)
    assert unfiltered["total"] == len(seed.all_audit_ids)
    assert set(_audit_ids(unfiltered)) == seed.all_audit_ids
    assert str(seed.config_audit_id) in _audit_ids(unfiltered)

    # 非法 agent_id 走既有入参校验口径（不静默忽略、不放行全量）
    malformed = await client.get(
        LIST_PATH, params={"agent_id": "not-a-uuid"}, headers=_headers(tenant)
    )
    assert malformed.status_code == 422, malformed.text
    assert malformed.json()["code"] == "COMMON_VALIDATION_ERROR"
    assert "items" not in (malformed.json().get("data") or {})


async def test_b213_list_agent_filter_keeps_pagination_and_total_consistent(
    client: AsyncClient, tenant: TenantContext, agent_filter_env: AgentFilterSeed
) -> None:
    """[B-213] 带 `agent_id` 时 total 与逐页 items 一致（分页不串行、不多不少）。"""
    seed = agent_filter_env
    first = await _list(client, tenant, agent_id=str(seed.agent_a_id), page=1, page_size=1)
    second = await _list(client, tenant, agent_id=str(seed.agent_a_id), page=2, page_size=1)
    assert first["total"] == second["total"] == len(seed.audit_a_ids)
    assert first["page"] == 1 and first["page_size"] == 1
    assert len(first["items"]) == len(second["items"]) == 1
    # ORDER BY occurred_at DESC：TOOL 行晚于 MODEL 行
    assert _audit_ids(first) == [str(seed.audit_a_ids[0])]
    assert _audit_ids(second) == [str(seed.audit_a_ids[1])]
    assert second["items"][0]["audit_type"] == "MODEL"


async def test_b213_export_accepts_agent_id_and_fingerprint_includes_it(
    client: AsyncClient, tenant: TenantContext, agent_filter_env: AgentFilterSeed
) -> None:
    """[B-213][RULE-09] API-05 接受 `agent_id`；同 key 换 Agent → 异指纹 `IDEMPOTENCY_MISMATCH`。"""
    seed = agent_filter_env
    key = f"export-{uuid.uuid4()}"
    body_a = {"export_format": "CSV", "agent_id": str(seed.agent_a_id)}

    first = await _post_export(client, tenant, key, body_a)
    assert first.status_code == 200, first.text
    first_data = first.json()["data"]
    assert first_data["status"] == "PENDING"

    conflicting = await _post_export(
        client, tenant, key, {"export_format": "CSV", "agent_id": str(seed.agent_b_id)}
    )
    assert conflicting.status_code == 409, conflicting.text
    assert conflicting.json()["code"] == "IDEMPOTENCY_MISMATCH"
    assert conflicting.json()["msg"] == MISMATCH_MESSAGE
    assert conflicting.json()["data"] is None

    # 同一 Agent 重放首次结果：指纹差异确实来自 agent_id 本身
    replay = await _post_export(client, tenant, key, body_a)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["export_id"] == first_data["export_id"]

    jobs = await _export_job_rows(tenant.tenant_id)
    assert len(jobs) == 1
    assert str(jobs[0]["id"]) == first_data["export_id"]
    assert jobs[0]["filters_json"] == {"agent_id": str(seed.agent_a_id)}
    assert await _count_rows(IDEMPOTENCY_TABLE, tenant.tenant_id) == 1
    # 非法 agent_id 被拒且不落任务行（校验口径与 API-01 一致）
    malformed = await _post_export(
        client, tenant, f"export-{uuid.uuid4()}", {"export_format": "CSV", "agent_id": "not-a-uuid"}
    )
    assert malformed.status_code == 422, malformed.text
    assert malformed.json()["code"] == "COMMON_VALIDATION_ERROR"
    assert len(await _export_job_rows(tenant.tenant_id)) == 1


async def test_b213_export_execution_applies_agent_id_filter(
    client: AsyncClient, tenant: TenantContext, agent_filter_env: AgentFilterSeed
) -> None:
    """[B-213] 导出产物只含该 Agent 的运行类行（筛选走落库 filters_json 全程生效）。"""
    seed = agent_filter_env
    created = await _post_export(
        client,
        tenant,
        f"export-{uuid.uuid4()}",
        {"export_format": "CSV", "agent_id": str(seed.agent_a_id)},
    )
    assert created.status_code == 200, created.text
    export_id = str(created.json()["data"]["export_id"])

    status = await _poll_until_terminal(client, tenant, export_id)
    assert status["status"] == "SUCCEEDED", status
    assert status["row_count"] == len(seed.audit_a_ids)

    download = await client.get(
        f"{EXPORT_PATH}/{export_id}/download", headers=_headers(tenant)
    )
    assert download.status_code == 200, download.text
    rows = _parse_csv(download.content)
    assert len(rows) == len(seed.audit_a_ids)
    assert sorted(row["audit_id"] for row in rows) == sorted(
        str(audit_id) for audit_id in seed.audit_a_ids
    )
    for row in rows:
        assert row["agent_id"] == str(seed.agent_a_id)
        assert row["agent_name"] == seed.agent_a_name
    # 干扰行（B 的运行类与 config 类）不得进入产物
    assert str(seed.audit_b_ids[0]) not in download.text
    assert str(seed.config_audit_id) not in download.text
