"""[S-05][E-05][API-05][RULE-09] 审计导出创建 API-05（真实 Console HTTP + 真实 PostgreSQL）。

S-05 边界：Browser→Console 导出创建 HTTP→幂等表与导出任务（PostgreSQL）；
E-05 边界：同一 `Idempotency-Key` 但筛选条件不同 → `IDEMPOTENCY_MISMATCH`，不创建第二个任务。
本文件只覆盖创建（任务行落库），导出执行/状态查询属 API-06。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from httpx import AsyncClient, Response
from muad_console_platform.infrastructure.db import get_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from console_platform.conftest import TenantContext

EXPORT_PATH = "/api/v1/audits/exports"
# RULE-09：endpoint 参与指纹，也是共享幂等表的定位列
EXPORT_ENDPOINT = EXPORT_PATH
JOB_TABLE = "control.audit_export_job"
IDEMPOTENCY_TABLE = "control.skill_import_idempotency"
FINGERPRINT_PREFIX = "sha256:"
DATE_TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
# catalog `config/api-messages.yaml` 的 IDEMPOTENCY_MISMATCH 文案（zh-CN）
MISMATCH_MESSAGE = "相同幂等键被用于不同的请求内容，请更换幂等键后重试"

# 筛选条件集合：A 覆盖枚举 + 时间出/入参归一，B 与 A 仅一处不同（异指纹）
FILTERS_A: dict[str, Any] = {
    "audit_type": "MODEL",
    "result_status": "FAILED",
    "start_time": "2026-09-01T00:00:00+00:00",
}
FILTERS_B: dict[str, Any] = {
    "audit_type": "MODEL",
    "result_status": "FAILED",
    "trace_id": "trace-other",
}
# 规范化后的 filters_json（unset 字段不落库，时间统一 UTC ISO8601）
CANONICAL_FILTERS_A = {
    "audit_type": "MODEL",
    "result_status": "FAILED",
    "start_time": "2026-09-01T00:00:00+00:00",
}

# 表名取自本模块字面量（不入参）；租户一律走绑定参数
_COUNT_SQL = "SELECT count(*) FROM {table} WHERE tenant_id = :tenant_id"
_SELECT_JOBS_SQL = (
    "SELECT id, tenant_id, created_by, export_format, filters_json, status,"
    " row_count, artifact_ref, error_code, is_deleted, create_time"
    f" FROM {JOB_TABLE} WHERE tenant_id = :tenant_id ORDER BY create_time"
)
_SELECT_IDEMPOTENCY_SQL = (
    "SELECT idempotency_key, endpoint, request_fingerprint, response_json, is_deleted"
    f" FROM {IDEMPOTENCY_TABLE} WHERE tenant_id = :tenant_id"
)


def _headers(tenant: TenantContext, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {"X-Tenant-Id": tenant.tenant_id}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _body(export_format: str, filters: dict[str, Any]) -> dict[str, Any]:
    return {"export_format": export_format, **filters}


async def _post_export(
    client: AsyncClient, tenant: TenantContext, key: str, body: dict[str, Any]
) -> Response:
    """[API-05] 提交导出创建请求（携带幂等键），返回原始响应供各断言分派。"""
    return await client.post(EXPORT_PATH, json=body, headers=_headers(tenant, key))


def _expected_fingerprint(
    tenant_id: str,
    account_id: uuid.UUID,
    export_format: str,
    filters: dict[str, Any],
) -> str:
    """RULE-09：规范化 JSON（sort_keys + 紧凑分隔符）的 SHA256，含 endpoint/tenant/actor/筛选条件。"""
    canonical = json.dumps(
        {
            "endpoint": EXPORT_ENDPOINT,
            "tenant_id": tenant_id,
            "created_by": str(account_id),
            "export_format": export_format,
            "filters": filters,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return FINGERPRINT_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _registered_account(session: AsyncSession, tenant_id: str) -> uuid.UUID:
    """创建接口的 `created_by` = 登录 `console_account.id`。"""
    row = (
        await session.execute(
            text(
                "SELECT id FROM control.console_account WHERE tenant_id = :tenant_id"
            ),
            {"tenant_id": tenant_id},
        )
    ).one()
    return row.id


async def _count_rows(session: AsyncSession, table: str, tenant_id: str) -> int:
    total = await session.scalar(
        text(_COUNT_SQL.format(table=table)), {"tenant_id": tenant_id}
    )
    return int(total or 0)


async def _job_rows(session: AsyncSession, tenant_id: str) -> list[dict[str, Any]]:
    rows = await session.execute(text(_SELECT_JOBS_SQL), {"tenant_id": tenant_id})
    return [dict(row) for row in rows.mappings()]


async def _idempotency_rows(session: AsyncSession, tenant_id: str) -> list[dict[str, Any]]:
    rows = await session.execute(text(_SELECT_IDEMPOTENCY_SQL), {"tenant_id": tenant_id})
    return [dict(row) for row in rows.mappings()]


@pytest.fixture
async def export_cleanup(tenant: TenantContext) -> AsyncIterator[None]:
    """导出任务行由本文件清理（共享幂等表行由 conftest 的 tenant 夹具清理）。"""
    try:
        yield
    finally:
        session_factory = get_session_factory()
        async with session_factory() as session:
            await session.execute(
                text(f"DELETE FROM {JOB_TABLE} WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant.tenant_id},
            )
            await session.commit()


async def _assert_single_job_row(
    tenant: TenantContext, account_id: uuid.UUID, first_data: dict[str, Any]
) -> None:
    """[E-05] 只落一行导出任务，且字段与首次响应/规范化筛选一致。"""
    session_factory = get_session_factory()
    async with session_factory() as session:
        assert await _count_rows(session, JOB_TABLE, tenant.tenant_id) == 1
        jobs = await _job_rows(session, tenant.tenant_id)
    row = jobs[0]
    assert str(row["id"]) == first_data["export_id"]
    assert row["status"] == "PENDING"
    assert row["created_by"] == account_id
    assert row["export_format"] == "CSV"
    # filters_json 规范化落库：unset 字段不落、时间统一 UTC ISO8601
    assert row["filters_json"] == CANONICAL_FILTERS_A
    assert row["row_count"] is None
    assert row["artifact_ref"] is None
    assert row["error_code"] is None
    assert row["is_deleted"] is False
    assert isinstance(row["create_time"], datetime)


async def _assert_single_idempotency_row(tenant: TenantContext, account_id: uuid.UUID, key: str) -> None:
    """[E-05][RULE-09] 幂等行只记一次提交，指纹为规范化载荷的 SHA256。"""
    session_factory = get_session_factory()
    async with session_factory() as session:
        idempotency = await _idempotency_rows(session, tenant.tenant_id)
    assert len(idempotency) == 1
    assert idempotency[0]["idempotency_key"] == key
    assert idempotency[0]["endpoint"] == EXPORT_ENDPOINT
    assert idempotency[0]["is_deleted"] is False
    # 指纹即"规范化 filters_json + endpoint/tenant/actor/format"的 SHA256
    assert idempotency[0]["request_fingerprint"] == _expected_fingerprint(
        tenant.tenant_id, account_id, "CSV", CANONICAL_FILTERS_A
    )


async def _assert_audit_routes_unaffected(client: AsyncClient, tenant: TenantContext) -> None:
    """[E-05] 路由顺序回归：详情路由仍在，列表查询不受 /exports 影响。"""
    listing = await client.get("/api/v1/audits", headers=_headers(tenant))
    assert listing.status_code == 200, listing.text
    assert listing.json()["code"] == "0"
    assert "items" in listing.json()["data"]

    detail = await client.get(
        f"/api/v1/audits/{uuid.uuid4()}",
        params={"audit_type": "MODEL"},
        headers=_headers(tenant),
    )
    assert detail.status_code == 404, detail.text
    assert detail.json()["code"] == "COMMON_NOT_FOUND"


async def test_e05_same_key_different_filters_is_idempotency_mismatch(
    client: AsyncClient, tenant: TenantContext, export_cleanup: None
) -> None:
    """[E-05][RULE-09] 同 key 异指纹 → 409 catalog 文案，且不产生第二个导出任务。"""
    session_factory = get_session_factory()
    async with session_factory() as session:
        account_id = await _registered_account(session, tenant.tenant_id)
    key = f"export-{uuid.uuid4()}"

    first = await _post_export(client, tenant, key, _body("CSV", FILTERS_A))
    assert first.status_code == 200, first.text
    first_data = first.json()["data"]
    assert first_data["status"] == "PENDING"
    assert DATE_TIME_PATTERN.match(str(first_data["create_time"])), first_data["create_time"]

    conflicting = await _post_export(client, tenant, key, _body("CSV", FILTERS_B))
    assert conflicting.status_code == 409, conflicting.text
    body = conflicting.json()
    assert body["code"] == "IDEMPOTENCY_MISMATCH"
    assert body["msg"] == MISMATCH_MESSAGE
    assert body["data"] is None

    await _assert_single_job_row(tenant, account_id, first_data)
    await _assert_single_idempotency_row(tenant, account_id, key)
    await _assert_audit_routes_unaffected(client, tenant)


async def test_s05_same_key_same_filters_replays_first_result(
    client: AsyncClient, tenant: TenantContext, export_cleanup: None
) -> None:
    """[S-05][RULE-09] 同 key 同指纹重放首次持久化结果，不重复建任务。"""
    key = f"export-{uuid.uuid4()}"
    body = _body("JSON", FILTERS_A)

    first = await client.post(EXPORT_PATH, json=body, headers=_headers(tenant, key))
    assert first.status_code == 200, first.text
    replay = await client.post(EXPORT_PATH, json=body, headers=_headers(tenant, key))
    assert replay.status_code == 200, replay.text

    first_data = first.json()["data"]
    replay_data = replay.json()["data"]
    assert replay_data["export_id"] == first_data["export_id"]
    assert replay_data["status"] == "PENDING"
    assert replay_data["create_time"] == first_data["create_time"]

    session_factory = get_session_factory()
    async with session_factory() as session:
        assert await _count_rows(session, JOB_TABLE, tenant.tenant_id) == 1
        jobs = await _job_rows(session, tenant.tenant_id)
        idempotency = await _idempotency_rows(session, tenant.tenant_id)
    assert str(jobs[0]["id"]) == first_data["export_id"]
    assert jobs[0]["export_format"] == "JSON"
    assert jobs[0]["filters_json"] == CANONICAL_FILTERS_A
    assert len(idempotency) == 1
    # 重放返回首次结果：幂等行只记一次提交
    assert idempotency[0]["endpoint"] == EXPORT_ENDPOINT
    assert idempotency[0]["response_json"]["export_id"] == first_data["export_id"]


async def test_api05_missing_idempotency_key_and_invalid_format_are_rejected(
    client: AsyncClient, tenant: TenantContext, export_cleanup: None
) -> None:
    """[API-05] 缺 `Idempotency-Key` 与非法 `export_format` 均被拒且不落任务行。"""
    missing = await client.post(EXPORT_PATH, json=_body("CSV", FILTERS_A), headers=_headers(tenant))
    assert missing.status_code == 422, missing.text
    assert missing.json()["code"] == "COMMON_VALIDATION_ERROR"

    bad_format = await client.post(
        EXPORT_PATH,
        json=_body("XML", FILTERS_A),
        headers=_headers(tenant, f"export-{uuid.uuid4()}"),
    )
    assert bad_format.status_code == 422, bad_format.text
    assert bad_format.json()["code"] == "COMMON_VALIDATION_ERROR"

    bad_filter = await client.post(
        EXPORT_PATH,
        json=_body("CSV", {"audit_type": "BOGUS"}),
        headers=_headers(tenant, f"export-{uuid.uuid4()}"),
    )
    assert bad_filter.status_code == 422, bad_filter.text
    assert bad_filter.json()["code"] == "COMMON_VALIDATION_ERROR"

    session_factory = get_session_factory()
    async with session_factory() as session:
        assert await _count_rows(session, JOB_TABLE, tenant.tenant_id) == 0
        assert await _count_rows(session, IDEMPOTENCY_TABLE, tenant.tenant_id) == 0
