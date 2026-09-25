"""审计导出任务创建（API-05）与状态/下载/执行（API-06）。

RULE-09：导出是创建类 POST（可重试提交），`Idempotency-Key` 必填；
首次提交在共享幂等表 `(tenant_id, idempotency_key, endpoint)` 记录指纹与结果，
并与 `control.audit_export_job`（`PENDING`）**同一事务**落库；重放读首次持久化结果。

API-06：状态查询按 `(tenant_id, export_id)` 读任务事实，仅 `SUCCEEDED` 可下载；
导出执行读取与列表 API-01 完全相同的四表 UNION ALL 投影（本层不另建宽表/投影），
产物经 artifact store 落盘（复用 skill 制品的 NFS 根与原子写），任务行只存 `artifact_ref`。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import AuditExportJob
from ..infrastructure.repositories.audit_export_repository import (
    EXPORT_STATUS_PENDING,
    EXPORT_STATUS_SUCCEEDED,
    AuditExportRepository,
)
from ..infrastructure.repositories.audit_query_repository import (
    PROJECTED_COLUMNS,
    AuditQueryFilters,
    AuditQueryRepository,
)
from ..infrastructure.skill_artifact_store import artifact_path, write_artifact
from .audit_query_service import RESULT_STATUSES, format_console_time, validate_filters
from .dto import AuditExportCreateRequest

_logger = logging.getLogger(__name__)

# 共享幂等表的 endpoint 定位值（设计 §3.3 / RULE-09）
IDEMPOTENCY_ENDPOINT = "/api/v1/audits/exports"
FINGERPRINT_PREFIX = "sha256:"

# 产物的 artifact store 前缀：与 skills/ 隔离，避免被 skill 孤儿文件清理误删
EXPORT_ARTIFACT_PREFIX = "exports"
# 落库 `export_format` → (文件扩展名, media type)；非法落库值按内部错误处理（不产出可疑产物）
JSON_EXTENSION = "json"
_EXPORT_FORMAT_SPECS: dict[str, tuple[str, str]] = {
    "CSV": ("csv", "text/csv"),
    "JSON": (JSON_EXTENSION, "application/json"),
}
# 创建接口（API-05）认可的请求格式与可落库格式同集合（单一事实源）
EXPORT_FORMATS = frozenset(_EXPORT_FORMAT_SPECS)
# 时间出参与 API-01 同口径（RULE-time-001）
_EXPORT_TIME_FIELDS = ("occurred_at", "started_at", "finished_at")
_EXPORT_UUID_FIELDS = ("audit_id", "resource_id", "actor_user_id", "agent_id")

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


def export_format_spec(store_format: str) -> tuple[str, str]:
    """落库的 `export_format` → `(扩展名, media type)`；非法落库值按内部错误处理。"""
    spec = _EXPORT_FORMAT_SPECS.get(store_format)
    if spec is None:
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return spec


def export_storage_key(tenant_id: str, export_id: uuid.UUID, store_format: str) -> str:
    """产物在 artifact store 的引用（= `artifact_ref`）：按租户/任务隔离，一任务一产物。"""
    extension, _media_type = export_format_spec(store_format)
    return f"{EXPORT_ARTIFACT_PREFIX}/{tenant_id}/{export_id}/audits.{extension}"


def export_download_filename(export_id: uuid.UUID, store_format: str) -> str:
    """设计 §3.4：`Content-Disposition` 文件名 `audits-{export_id}.{csv|json}`。"""
    extension, _media_type = export_format_spec(store_format)
    return f"audits-{export_id}.{extension}"


def _canonical_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError as error:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from error


def _canonical_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as error:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from error


def query_filters_from_canonical(canonical: Mapping[str, Any]) -> AuditQueryFilters:
    """`filters_json`（规范化落库值）→ API-01 同构筛选条件。

    执行期只信落库值：非法载荷（非对象、非法 UUID/时间）按校验失败处理并落 `error_code`。
    """
    if not isinstance(canonical, Mapping):
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    return AuditQueryFilters(
        audit_type=canonical.get("audit_type"),
        resource_type=canonical.get("resource_type"),
        resource_id=_canonical_uuid(canonical.get("resource_id")),
        actor_user_id=_canonical_uuid(canonical.get("actor_user_id")),
        action=canonical.get("action"),
        result_status=canonical.get("result_status"),
        trace_id=canonical.get("trace_id"),
        start_time=_canonical_datetime(canonical.get("start_time")),
        end_time=_canonical_datetime(canonical.get("end_time")),
    )


def _export_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """投影行 → 产物行：只取统一字段集，时间出参与 API-01 同格式，UUID 归一为字符串。"""
    item = {column: row[column] for column in PROJECTED_COLUMNS}
    for field in _EXPORT_TIME_FIELDS:
        item[field] = format_console_time(row[field])
    for field in _EXPORT_UUID_FIELDS:
        item[field] = None if row[field] is None else str(row[field])
    return item


def serialize_export_rows(
    rows: Sequence[Mapping[str, Any]], store_format: str
) -> bytes:
    """产物序列化：CSV 表头/JSON 键均为统一字段集，不含任何未投影内容。"""
    extension, _media_type = export_format_spec(store_format)
    normalized = [_export_row(row) for row in rows]
    if extension == JSON_EXTENSION:
        return json.dumps(normalized, ensure_ascii=False).encode("utf-8")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(PROJECTED_COLUMNS), lineterminator="\n")
    writer.writeheader()
    writer.writerows(normalized)
    return buffer.getvalue().encode("utf-8")


def _read_artifact(storage_key: str) -> bytes:
    """读回产物字节流；产物缺失/不可读按内部错误处理（不降级为空文件）。"""
    try:
        return artifact_path(storage_key).read_bytes()
    except OSError as error:
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from error


def _export_status_data(job: AuditExportJob) -> dict[str, Any]:
    """设计 §3.4 API-06 状态出参（`status=FAILED` 时 `error_code` 为 catalog 错误码）。"""
    return {
        "export_id": str(job.id),
        "status": job.status,
        "row_count": job.row_count,
        "error_code": job.error_code,
        "create_time": format_console_time(job.create_time),
        "update_time": format_console_time(job.update_time),
    }


@dataclass(frozen=True)
class ExportArtifact:
    """下载产物：文件名、media type 与来自 artifact store 的字节流。"""

    filename: str
    media_type: str
    content: bytes


class AuditExportService:
    def __init__(self, session: AsyncSession, registered_codes: frozenset[str]) -> None:
        self._session = session
        self._repository = AuditExportRepository(session)
        self._query_repository = AuditQueryRepository(session)
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

    async def get_export_status(self, tenant_id: str, export_id: uuid.UUID) -> dict[str, Any]:
        """API-06 状态：按 `(tenant_id, export_id)` 读任务事实；不存在/跨租户 → NOT_FOUND。"""
        job = await self._find_job(tenant_id, export_id)
        return _export_status_data(job)

    async def get_export_download(self, tenant_id: str, export_id: uuid.UUID) -> ExportArtifact:
        """API-06 下载：仅 `SUCCEEDED` 可下载（未完成 `COMMON_CONFLICT`）。

        返回的字节流即执行期写入 artifact store 的产物，不追加任何审计明细之外的字段。
        """
        job = await self._find_job(tenant_id, export_id)
        if job.status != EXPORT_STATUS_SUCCEEDED or job.artifact_ref is None:
            raise AppError(ErrorCode.COMMON_CONFLICT)
        _extension, media_type = export_format_spec(job.export_format)
        return ExportArtifact(
            filename=export_download_filename(job.id, job.export_format),
            media_type=media_type,
            content=_read_artifact(job.artifact_ref),
        )

    async def run_pending_exports(self, tenant_id: str) -> int:
        """导出执行器：按 `(tenant_id, status, create_time)` 领取待处理任务并逐个执行。

        触发方式为**状态查询请求内惰性驱动**（V1 不引入独立调度进程，见 §3.3 任务表说明）；
        行锁 + `SKIP LOCKED` 使多副本并发轮询同一任务时只执行一次。返回执行的任务数。
        """
        jobs = await self._repository.claim_pending_jobs(tenant_id)
        for job in jobs:
            await self._repository.mark_running(job)
            await self._execute(job)
        return len(jobs)

    async def _find_job(self, tenant_id: str, export_id: uuid.UUID) -> AuditExportJob:
        job = await self._repository.find_job(tenant_id, export_id)
        if job is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return job

    async def _execute(self, job: AuditExportJob) -> None:
        """取数 → 序列化 → 落 artifact store → 回填状态。

        执行期失败不抛错：失败原因作为 catalog error_code 落库（可轮询到 FAILED）。
        """
        try:
            rows = await self._load_export_rows(job)
            content = serialize_export_rows(rows, job.export_format)
            storage_key = export_storage_key(job.tenant_id, job.id, job.export_format)
            write_artifact(storage_key, content)
        except AppError as error:
            await self._fail(job, error.code)
        except OSError:
            _logger.exception("audit_export_artifact_write_failed export_id=%s", job.id)
            await self._fail(job, ErrorCode.COMMON_INTERNAL_ERROR)
        else:
            await self._repository.mark_succeeded(
                job, row_count=len(rows), artifact_ref=storage_key
            )

    async def _fail(self, job: AuditExportJob, error_code: str) -> None:
        _logger.warning("audit_export_failed export_id=%s code=%s", job.id, error_code)
        await self._repository.mark_failed(job, error_code)

    async def _load_export_rows(self, job: AuditExportJob) -> list[dict[str, Any]]:
        """与列表 API-01 共用同一四表 UNION ALL 投影，只按任务落库的筛选条件取数。"""
        filters = query_filters_from_canonical(job.filters_json)
        validate_filters(filters, self._allowed_result_statuses)
        return await self._query_repository.all_rows(job.tenant_id, filters)

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
