"""[B-202] 审计导出状态/下载 API-06（真实 Console HTTP + 真实 PostgreSQL + 真实 artifact store）。

B-202 边界：Console HTTP 导出状态/下载 → 四表聚合投影（PostgreSQL）→ 临时 artifact root 产物。
本文件不 mock 业务 API（不 patch service/repository）；导出执行器由状态查询接口惰性驱动。
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
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
from muad_console_platform.main import app
from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncSession

from console_platform.conftest import TenantContext

EXPORT_PATH = "/api/v1/audits/exports"
JOB_TABLE = "control.audit_export_job"
STATUS_FIELDS = frozenset(
    {"export_id", "status", "row_count", "error_code", "create_time", "update_time"}
)
TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED"})
DATE_TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
POLL_ATTEMPTS = 5
POLL_INTERVAL_SEC = 0.05

# 产物列集 = API-01 统一投影字段：不多一列（无 before/after、无 args_preview）
PROJECTED_FIELDS = (
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
# 只写入未投影的 jsonb 列（config after_json / tool args_preview_json）：产物不得含该值
SECRET_VALUE = "sk-live-2f9c4a-audit-secret"

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
            'export seed', :trace_id, now(), now(), false)
    """
)

_INSERT_CONFIG_AUDIT = text(
    """
    INSERT INTO control.config_audit_log
        (id, tenant_id, actor_user_id, resource_type, resource_id, action,
         before_json, after_json, trace_id, create_time)
    VALUES (:audit_id, :tenant_id, :actor_user_id, 'AGENT', :agent_id, 'UPDATE',
            NULL, CAST(:after_json AS jsonb), :trace_id, :occurred_at)
    """
)

_INSERT_TOOL_AUDIT = text(
    """
    INSERT INTO runtime.tool_call_audit
        (id, tenant_id, run_id, task_id, conversation_id, tool_call_id, tool_name,
         tool_kind, prepared_args_hash, args_preview_json, status, start_time,
         end_time, latency_ms, create_time)
    VALUES (:audit_id, :tenant_id, :run_id, NULL, :conversation_id, 'call-1',
            'execute_skill', 'SKILL', 'sha256:seed', CAST(:args_preview AS jsonb), 'OK',
            :occurred_at, :occurred_at, 812, :occurred_at)
    """
)

# 注入失败用：真实落库的 PENDING 任务 + 非法来源表（执行期校验必然失败）
_INSERT_JOB = text(
    """
    INSERT INTO control.audit_export_job
        (id, tenant_id, created_by, export_format, filters_json, status)
    VALUES (:export_id, :tenant_id, :created_by, :export_format,
            CAST(:filters_json AS jsonb), 'PENDING')
    """
)

_SELECT_JOB = text(
    """
    SELECT status, row_count, artifact_ref, error_code
      FROM control.audit_export_job
     WHERE tenant_id = :tenant_id
       AND id = :export_id
    """
)

_CLEANUP_STATEMENTS = (
    f"DELETE FROM {JOB_TABLE} WHERE tenant_id = :tenant_id",
    "DELETE FROM runtime.tool_call_audit WHERE tenant_id = :tenant_id",
    "DELETE FROM runtime.run_record WHERE tenant_id = :tenant_id",
    "DELETE FROM runtime.conversation WHERE tenant_id = :tenant_id",
    "DELETE FROM control.config_audit_log WHERE tenant_id = :tenant_id",
    "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id",
    "DELETE FROM control.agent_definition WHERE tenant_id = :tenant_id",
)


@dataclass(frozen=True)
class ExportSeed:
    """一个租户内的目标 trace（CONFIG+TOOL）与干扰 trace（CONFIG）审计事实。"""

    trace_id: str
    other_trace_id: str
    audit_ids: tuple[uuid.UUID, uuid.UUID]
    other_audit_id: uuid.UUID
    account_id: uuid.UUID
    artifact_root: Path

    @property
    def expected_ids(self) -> tuple[str, str]:
        return (str(self.audit_ids[0]), str(self.audit_ids[1]))

    @property
    def row_count(self) -> int:
        return len(self.audit_ids)


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


async def _registered_account(session: AsyncSession, tenant_id: str) -> uuid.UUID:
    row = (
        await session.execute(
            text("SELECT id FROM control.console_account WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
    ).one()
    return row.id


async def _insert(session: AsyncSession, statement: TextClause, params: dict[str, Any]) -> None:
    await session.execute(statement, params)


async def _seed_agent(
    session: AsyncSession, tenant: TenantContext, agent_id: uuid.UUID
) -> None:
    await _insert(
        session,
        _INSERT_AGENT,
        {
            "agent_id": agent_id,
            "tenant_id": tenant.tenant_id,
            "key": f"export-agent-{agent_id.hex[:8]}",
            "agent_name": "Export Agent",
            "model_id": tenant.model_id,
        },
    )


async def _seed_platform_user(
    session: AsyncSession, tenant: TenantContext, user_id: uuid.UUID
) -> None:
    await _insert(
        session,
        _INSERT_PLATFORM_USER,
        {
            "user_id": user_id,
            "tenant_id": tenant.tenant_id,
            "user_code": f"export-user-{user_id.hex[:8]}",
            "user_name": "Export User",
        },
    )


async def _seed_conversation(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> None:
    await _insert(
        session,
        _INSERT_CONVERSATION,
        {
            "conversation_id": conversation_id,
            "tenant_id": tenant.tenant_id,
            "user_id": user_id,
            "agent_id": agent_id,
        },
    )


async def _seed_run(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    trace_id: str,
) -> None:
    await _insert(
        session,
        _INSERT_RUN,
        {
            "run_id": run_id,
            "tenant_id": tenant.tenant_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "trace_id": trace_id,
        },
    )


async def _seed_config_audit(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    audit_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    agent_id: uuid.UUID,
    trace_id: str,
    after_json: str | None,
    occurred_at: datetime,
) -> None:
    await _insert(
        session,
        _INSERT_CONFIG_AUDIT,
        {
            "audit_id": audit_id,
            "tenant_id": tenant.tenant_id,
            "actor_user_id": actor_user_id,
            "agent_id": agent_id,
            "trace_id": trace_id,
            "after_json": after_json,
            "occurred_at": occurred_at,
        },
    )


async def _seed_tool_audit(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    audit_id: uuid.UUID,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
    args_preview: str,
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
            "args_preview": args_preview,
            "occurred_at": occurred_at,
        },
    )


async def _seed_audit_facts(
    session: AsyncSession,
    tenant: TenantContext,
    seed: ExportSeed,
    *,
    agent_id: uuid.UUID,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> None:
    base = datetime.now(UTC)
    await _seed_config_audit(
        session,
        tenant,
        audit_id=seed.audit_ids[0],
        actor_user_id=seed.account_id,
        agent_id=agent_id,
        trace_id=seed.trace_id,
        after_json=json.dumps({"api_key": SECRET_VALUE}),
        occurred_at=base - timedelta(minutes=2),
    )
    await _seed_tool_audit(
        session,
        tenant,
        audit_id=seed.audit_ids[1],
        run_id=run_id,
        conversation_id=conversation_id,
        args_preview=json.dumps({"headers": {"Authorization": f"Bearer {SECRET_VALUE}"}}),
        occurred_at=base - timedelta(minutes=1),
    )
    await _seed_config_audit(
        session,
        tenant,
        audit_id=seed.other_audit_id,
        actor_user_id=seed.account_id,
        agent_id=agent_id,
        trace_id=seed.other_trace_id,
        after_json=None,
        occurred_at=base,
    )


async def _seed(session: AsyncSession, tenant: TenantContext, seed: ExportSeed) -> None:
    """按外键依赖顺序落前置实体，再写入两条目标 trace 行 + 一条干扰 trace 行。"""
    agent_id, user_id, run_id, conversation_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    await _seed_agent(session, tenant, agent_id)
    await _seed_platform_user(session, tenant, user_id)
    await _seed_conversation(
        session,
        tenant,
        conversation_id=conversation_id,
        user_id=user_id,
        agent_id=agent_id,
    )
    await _seed_run(
        session,
        tenant,
        run_id=run_id,
        conversation_id=conversation_id,
        user_id=user_id,
        agent_id=agent_id,
        trace_id=seed.trace_id,
    )
    await _seed_audit_facts(
        session,
        tenant,
        seed,
        agent_id=agent_id,
        run_id=run_id,
        conversation_id=conversation_id,
    )


async def _insert_job(
    tenant_id: str,
    export_id: uuid.UUID,
    *,
    filters_json: dict[str, Any],
    export_format: str = "CSV",
) -> None:
    """直接落库一行 PENDING 任务（故障注入/跨租户夹具用；不经过创建接口）。"""
    session_factory = get_session_factory()
    async with session_factory() as session:
        await _insert(
            session,
            _INSERT_JOB,
            {
                "export_id": export_id,
                "tenant_id": tenant_id,
                "created_by": uuid.uuid4(),
                "export_format": export_format,
                "filters_json": json.dumps(filters_json),
            },
        )
        await session.commit()


async def _job_row(tenant_id: str, export_id: uuid.UUID) -> dict[str, Any] | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        row = (
            await session.execute(
                _SELECT_JOB, {"tenant_id": tenant_id, "export_id": export_id}
            )
        ).mappings().first()
    return None if row is None else dict(row)


@pytest.fixture
async def export_env(
    client: AsyncClient, tenant: TenantContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[ExportSeed]:
    """真实审计事实 + 临时 artifact root（产物不落仓库 ./.data/artifacts）。"""
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    session_factory = get_session_factory()
    async with session_factory() as session:
        seed = ExportSeed(
            trace_id=f"trace-{uuid.uuid4().hex}",
            other_trace_id=f"trace-{uuid.uuid4().hex}",
            audit_ids=(uuid.uuid4(), uuid.uuid4()),
            other_audit_id=uuid.uuid4(),
            account_id=await _registered_account(session, tenant.tenant_id),
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


async def _create_export(
    client: AsyncClient, tenant: TenantContext, seed: ExportSeed, export_format: str
) -> tuple[str, dict[str, Any]]:
    response = await client.post(
        EXPORT_PATH,
        json={"export_format": export_format, "trace_id": seed.trace_id},
        headers={**_headers(tenant), "Idempotency-Key": f"export-{uuid.uuid4()}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["code"] == "0"
    data = response.json()["data"]
    return str(data["export_id"]), data


async def _fetch_status(
    client: AsyncClient, tenant: TenantContext, export_id: str
) -> Response:
    return await client.get(f"{EXPORT_PATH}/{export_id}", headers=_headers(tenant))


async def _poll_until_terminal(
    client: AsyncClient, tenant: TenantContext, export_id: str
) -> dict[str, Any]:
    """轮询状态接口直至终态：不得手工改库，状态必须由请求链路自行推进。"""
    data: dict[str, Any] = {}
    for _ in range(POLL_ATTEMPTS):
        response = await _fetch_status(client, tenant, export_id)
        assert response.status_code == 200, response.text
        assert response.json()["code"] == "0"
        data = response.json()["data"]
        if data["status"] in TERMINAL_STATUSES:
            return data
        await asyncio.sleep(POLL_INTERVAL_SEC)
    return data


async def _download(
    client: AsyncClient, tenant: TenantContext, export_id: str
) -> Response:
    return await client.get(
        f"{EXPORT_PATH}/{export_id}/download", headers=_headers(tenant)
    )


def _media_type(response: Response) -> str:
    return response.headers["content-type"].split(";")[0].strip()


def _artifact_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("audits.*") if path.is_file()]


def _parse_csv(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    return list(reader.fieldnames or []), [dict(row) for row in reader]


def _assert_status_envelope(data: dict[str, Any], export_id: str) -> None:
    assert set(data) == STATUS_FIELDS, data
    assert data["export_id"] == export_id
    assert DATE_TIME_PATTERN.match(str(data["create_time"])), data["create_time"]
    assert DATE_TIME_PATTERN.match(str(data["update_time"])), data["update_time"]


async def test_b202_export_status_progresses_and_download_returns_artifact(
    client: AsyncClient, tenant: TenantContext, export_env: ExportSeed
) -> None:
    """[B-202] 轮询至 SUCCEEDED（行数=筛选命中数）→ 下载返回 CSV 产物与正确响应头。"""
    export_id, created = await _create_export(client, tenant, export_env, "CSV")
    assert created["status"] == "PENDING"

    status = await _poll_until_terminal(client, tenant, export_id)
    assert status["status"] == "SUCCEEDED", status
    assert status["row_count"] == export_env.row_count
    assert status["error_code"] is None
    _assert_status_envelope(status, export_id)

    download = await _download(client, tenant, export_id)
    assert download.status_code == 200, download.text
    assert _media_type(download) == "text/csv"
    assert (
        download.headers["content-disposition"]
        == f'attachment; filename="audits-{export_id}.csv"'
    )

    fieldnames, rows = _parse_csv(download.content)
    assert fieldnames == list(PROJECTED_FIELDS)
    assert sorted(row["audit_id"] for row in rows) == sorted(export_env.expected_ids)
    # 行序与 API-01 一致（occurred_at DESC）：seed 的 TOOL 行晚于 CONFIG 行
    assert [row["audit_type"] for row in rows] == ["TOOL", "CONFIG"]
    for row in rows:
        assert row["trace_id"] == export_env.trace_id
        assert row["result_status"] == "SUCCESS"
        assert DATE_TIME_PATTERN.match(row["occurred_at"]), row["occurred_at"]
    # 干扰 trace 的行不得出现在产物里；产物不含任何未投影列的值（Secret）
    assert str(export_env.other_audit_id) not in download.text
    assert export_env.other_trace_id not in download.text
    assert SECRET_VALUE not in download.text
    assert "after_json" not in download.text

    # 产物真实落盘在临时 artifact root，且下载流与该文件逐字节一致
    artifacts = _artifact_files(export_env.artifact_root)
    assert len(artifacts) == 1, artifacts
    assert artifacts[0].read_bytes() == download.content
    row = await _job_row(tenant.tenant_id, uuid.UUID(export_id))
    assert row is not None
    assert row["status"] == "SUCCEEDED"
    assert row["row_count"] == export_env.row_count
    assert row["artifact_ref"] is not None
    assert row["error_code"] is None


async def test_b202_json_export_download_parses_as_json(
    client: AsyncClient, tenant: TenantContext, export_env: ExportSeed
) -> None:
    """[B-202] `export_format=JSON` 时产物为 JSON 数组，响应头与文件名随格式切换。"""
    export_id, _ = await _create_export(client, tenant, export_env, "JSON")
    status = await _poll_until_terminal(client, tenant, export_id)
    assert status["status"] == "SUCCEEDED", status

    download = await _download(client, tenant, export_id)
    assert download.status_code == 200, download.text
    assert _media_type(download) == "application/json"
    assert (
        download.headers["content-disposition"]
        == f'attachment; filename="audits-{export_id}.json"'
    )
    rows = json.loads(download.content)
    assert isinstance(rows, list)
    assert len(rows) == export_env.row_count
    for row in rows:
        assert set(row) == set(PROJECTED_FIELDS)
        assert row["trace_id"] == export_env.trace_id
    assert SECRET_VALUE not in download.text


async def test_b202_download_before_completion_is_conflict(
    client: AsyncClient, tenant: TenantContext, export_env: ExportSeed
) -> None:
    """[B-202] 未完成的任务不可下载：创建后任务事实仍为 PENDING → `COMMON_CONFLICT`。"""
    export_id, _ = await _create_export(client, tenant, export_env, "CSV")
    row = await _job_row(tenant.tenant_id, uuid.UUID(export_id))
    assert row is not None and row["status"] == "PENDING"
    assert _artifact_files(export_env.artifact_root) == []

    download = await _download(client, tenant, export_id)
    assert download.status_code == 409, download.text
    body = download.json()
    assert body["code"] == "COMMON_CONFLICT"
    assert body["msg"] == "数据已发生变化，请刷新后重试"
    assert body["data"] is None
    assert SECRET_VALUE not in download.text


async def _seed_own_job(
    client: AsyncClient, tenant: TenantContext, seed: ExportSeed
) -> uuid.UUID:
    # 正向对照：本租户自己的任务按 (tenant_id, export_id) 可读，证明 404 不是路由缺失
    own_export_id = uuid.uuid4()
    await _insert_job(
        tenant.tenant_id, own_export_id, filters_json={"trace_id": seed.trace_id}
    )
    own = await _fetch_status(client, tenant, str(own_export_id))
    assert own.status_code == 200, own.text
    assert own.json()["data"]["status"] == "SUCCEEDED"
    return own_export_id


async def _assert_other_job_untouched(
    other_tenant_id: str,
    other_export_id: uuid.UUID,
    *,
    artifact_root: Path,
    own_export_id: uuid.UUID,
) -> None:
    # 他人任务未被本租户的请求执行/改写：仍为 PENDING 且无任何产物
    row = await _job_row(other_tenant_id, other_export_id)
    assert row == {
        "status": "PENDING",
        "row_count": None,
        "artifact_ref": None,
        "error_code": None,
    }
    artifacts = _artifact_files(artifact_root)
    assert [path for path in artifacts if str(other_export_id) in str(path)] == []
    assert [path for path in artifacts if str(own_export_id) in str(path)]


async def _cleanup_tenant_jobs(tenant_id: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            text(f"DELETE FROM {JOB_TABLE} WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await session.commit()


async def test_b202_unknown_and_cross_tenant_export_is_not_found(
    client: AsyncClient, tenant: TenantContext, export_env: ExportSeed
) -> None:
    """[B-202] 未知任务与跨租户任务一律 `COMMON_NOT_FOUND`，且不泄漏、不执行他人任务。"""
    own_export_id = await _seed_own_job(client, tenant, export_env)

    unknown_id = str(uuid.uuid4())
    unknown = await _fetch_status(client, tenant, unknown_id)
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["code"] == "COMMON_NOT_FOUND"
    assert unknown.json()["data"] is None

    other_tenant_id = f"other-{uuid.uuid4()}"
    other_export_id = uuid.uuid4()
    await _insert_job(other_tenant_id, other_export_id, filters_json={})
    try:
        cross_status = await _fetch_status(client, tenant, str(other_export_id))
        assert cross_status.status_code == 404, cross_status.text
        assert cross_status.json()["code"] == "COMMON_NOT_FOUND"
        assert cross_status.json()["data"] is None

        cross_download = await _download(client, tenant, str(other_export_id))
        assert cross_download.status_code == 404, cross_download.text
        assert cross_download.json()["code"] == "COMMON_NOT_FOUND"
        assert cross_download.json()["data"] is None

        await _assert_other_job_untouched(
            other_tenant_id,
            other_export_id,
            artifact_root=export_env.artifact_root,
            own_export_id=own_export_id,
        )
    finally:
        await _cleanup_tenant_jobs(other_tenant_id)


async def test_b202_failed_execution_reports_catalog_error_code(
    client: AsyncClient, tenant: TenantContext, export_env: ExportSeed
) -> None:
    """[B-202] 执行失败 → `FAILED` + catalog `error_code`；产物不落盘且仍不可下载。"""
    export_id = uuid.uuid4()
    # 故障注入：真实落库的任务带着非法来源表（`BOGUS`），执行期校验必然失败
    await _insert_job(
        tenant.tenant_id, export_id, filters_json={"audit_type": "BOGUS"}
    )

    status = await _poll_until_terminal(client, tenant, str(export_id))
    assert status["status"] == "FAILED", status
    assert status["error_code"] == "COMMON_VALIDATION_ERROR"
    assert status["row_count"] is None
    _assert_status_envelope(status, str(export_id))
    assert "COMMON_VALIDATION_ERROR" in app.state.message_catalog.codes()

    row = await _job_row(tenant.tenant_id, export_id)
    assert row is not None
    assert row["status"] == "FAILED"
    assert row["error_code"] == "COMMON_VALIDATION_ERROR"
    assert row["artifact_ref"] is None
    assert _artifact_files(export_env.artifact_root) == []

    download = await _download(client, tenant, str(export_id))
    assert download.status_code == 409, download.text
    assert download.json()["code"] == "COMMON_CONFLICT"
