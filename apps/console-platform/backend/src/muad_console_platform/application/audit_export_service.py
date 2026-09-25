"""审计导出任务创建服务（API-05）。

RULE-09：导出是创建类 POST（可重试提交），`Idempotency-Key` 必填；
首次提交在共享幂等表 `(tenant_id, idempotency_key, endpoint)` 记录指纹与结果，
并与 `control.audit_export_job`（`PENDING`）**同一事务**落库；重放读首次持久化结果。
本模块只创建任务行，导出执行与状态/下载查询属 API-06。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import AuditExportJob
from ..infrastructure.repositories.audit_export_repository import (
    EXPORT_STATUS_PENDING,
    AuditExportRepository,
)
from ..infrastructure.repositories.audit_query_repository import AuditQueryFilters
from .audit_query_service import RESULT_STATUSES, format_console_time, validate_filters
from .dto import AuditExportCreateRequest

# 共享幂等表的 endpoint 定位值（设计 §3.3 / RULE-09）
IDEMPOTENCY_ENDPOINT = "/api/v1/audits/exports"
EXPORT_FORMATS = frozenset({"CSV", "JSON"})
FINGERPRINT_PREFIX = "sha256:"

# 与 API-01 一致的筛选字段（不含旧版兼容的 keyword：API-05 请求体不暴露该字段）
_FILTER_FIELDS = (
    "audit_type",
    "resource_type",
    "resource_id",
    "actor_user_id",
    "action",
    "result_status",
    "trace_id",
    "start_time",
    "end_time",
)


def _canonical_time(value: datetime) -> str:
    """时间归一为 UTC ISO8601：同一时刻的不同写法必须产生同一指纹。"""
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat()


def canonical_filters(filters: AuditQueryFilters) -> dict[str, Any]:
    """规范化筛选条件：未设置字段不落库，UUID/时间归一为字符串。

    返回的字典即 `filters_json` 的落库内容，也是指纹输入中的 `filters`。
    """
    canonical: dict[str, Any] = {}
    for field in _FILTER_FIELDS:
        value = getattr(filters, field)
        if value is None:
            continue
        canonical[field] = _canonical_time(value) if isinstance(value, datetime) else str(value)
    return canonical


def request_fingerprint(
    tenant_id: str,
    created_by: uuid.UUID,
    export_format: str,
    canonical: dict[str, Any],
) -> str:
    """RULE-09 指纹：规范化 JSON（sort_keys + 紧凑分隔符）的 SHA256。"""
    body = json.dumps(
        {
            "endpoint": IDEMPOTENCY_ENDPOINT,
            "tenant_id": tenant_id,
            "created_by": str(created_by),
            "export_format": export_format,
            "filters": canonical,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return FINGERPRINT_PREFIX + hashlib.sha256(body.encode("utf-8")).hexdigest()


def to_query_filters(payload: AuditExportCreateRequest) -> AuditQueryFilters:
    """请求体 → API-01 同构筛选条件（校验与指纹都基于同一份值）。"""
    return AuditQueryFilters(
        audit_type=payload.audit_type,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        actor_user_id=payload.actor_user_id,
        action=payload.action,
        result_status=payload.result_status,
        trace_id=payload.trace_id,
        start_time=payload.start_time,
        end_time=payload.end_time,
    )


def export_format(value: str) -> str:
    if value not in EXPORT_FORMATS:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    return value


class AuditExportService:
    def __init__(self, session: AsyncSession, registered_codes: frozenset[str]) -> None:
        self._session = session
        self._repository = AuditExportRepository(session)
        self._allowed_result_statuses = RESULT_STATUSES | registered_codes

    async def create_export(
        self,
        tenant_id: str,
        created_by: uuid.UUID,
        payload: AuditExportCreateRequest,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """校验 → 锁 → 重放或首次创建；返回 `{export_id, status, create_time}`。"""
        requested_format = export_format(payload.export_format)
        filters = to_query_filters(payload)
        validate_filters(filters, self._allowed_result_statuses)
        canonical = canonical_filters(filters)
        fingerprint = request_fingerprint(tenant_id, created_by, requested_format, canonical)
        await self._lock_idempotency(tenant_id, idempotency_key)
        replayed = await self._repository.find_idempotency(
            tenant_id, idempotency_key, IDEMPOTENCY_ENDPOINT
        )
        if replayed is not None:
            if replayed.request_fingerprint != fingerprint:
                raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
            return dict(replayed.response_json)
        return await self._create_once(
            tenant_id, created_by, requested_format, canonical, idempotency_key, fingerprint
        )

    async def _create_once(
        self,
        tenant_id: str,
        created_by: uuid.UUID,
        requested_format: str,
        canonical: dict[str, Any],
        idempotency_key: str,
        fingerprint: str,
    ) -> dict[str, Any]:
        """首次提交：任务行与幂等行同事务写入（提交由 `get_session` 依赖完成）。"""
        job = await self._repository.add_job(
            AuditExportJob(
                tenant_id=tenant_id,
                created_by=created_by,
                export_format=requested_format,
                filters_json=canonical,
                status=EXPORT_STATUS_PENDING,
            )
        )
        data = {
            "export_id": str(job.id),
            "status": job.status,
            "create_time": format_console_time(job.create_time),
        }
        await self._repository.add_idempotency(
            tenant_id, idempotency_key, IDEMPOTENCY_ENDPOINT, fingerprint, data
        )
        return data

    async def _lock_idempotency(self, tenant_id: str, idempotency_key: str) -> None:
        """同一 (tenant, key, endpoint) 串行化：并发由 partial unique 兜底，落败者重放首次结果。"""
        digest = hashlib.sha256(
            f"{tenant_id}|{idempotency_key}|{IDEMPOTENCY_ENDPOINT}".encode()
        ).digest()
        lock_key = int.from_bytes(digest[:8], "big", signed=True)
        await self._session.execute(select(func.pg_advisory_xact_lock(lock_key)))
