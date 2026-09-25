"""审计导出任务事实与共享幂等表的读写（API-05 / RULE-09）。

任务行与幂等行由服务层在**同一事务**写入（会话提交由 `get_session` 依赖完成）；
本层只 `flush` 不提交，保证"首次提交记录 + 导出任务"原子落库。

API-06 的状态推进同样只 `flush`：执行器与状态查询共处一个请求事务，
状态机 `PENDING → RUNNING → SUCCEEDED/FAILED` 随请求提交原子落库。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import AuditExportJob, SkillImportIdempotency

EXPORT_STATUS_PENDING = "PENDING"
EXPORT_STATUS_RUNNING = "RUNNING"
EXPORT_STATUS_SUCCEEDED = "SUCCEEDED"
EXPORT_STATUS_FAILED = "FAILED"


class AuditExportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_job(self, job: AuditExportJob) -> AuditExportJob:
        self._session.add(job)
        await self._session.flush()
        # create_time/update_time 为 server_default，刷新后出参才是真实落库时刻
        await self._session.refresh(job)
        return job

    async def find_idempotency(
        self, tenant_id: str, idempotency_key: str, endpoint: str
    ) -> SkillImportIdempotency | None:
        """共享幂等表按 `(tenant_id, idempotency_key, endpoint)` partial unique 定位首次提交。"""
        record: SkillImportIdempotency | None = await self._session.scalar(
            select(SkillImportIdempotency).where(
                SkillImportIdempotency.tenant_id == tenant_id,
                SkillImportIdempotency.idempotency_key == idempotency_key,
                SkillImportIdempotency.endpoint == endpoint,
                SkillImportIdempotency.is_deleted.is_(False),
            )
        )
        return record

    async def add_idempotency(
        self,
        tenant_id: str,
        idempotency_key: str,
        endpoint: str,
        fingerprint: str,
        response: dict[str, Any],
    ) -> None:
        self._session.add(
            SkillImportIdempotency(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                endpoint=endpoint,
                request_fingerprint=fingerprint,
                response_json=response,
            )
        )
        await self._session.flush()

    async def find_job(self, tenant_id: str, export_id: uuid.UUID) -> AuditExportJob | None:
        """API-06：按 `(tenant_id, export_id)` 读任务事实；跨租户/已软删一律不可见。"""
        job: AuditExportJob | None = await self._session.scalar(
            select(AuditExportJob).where(
                AuditExportJob.tenant_id == tenant_id,
                AuditExportJob.id == export_id,
                AuditExportJob.is_deleted.is_(False),
            )
        )
        return job

    async def claim_pending_jobs(self, tenant_id: str) -> list[AuditExportJob]:
        """按 `(tenant_id, status, create_time)` 取待处理任务并加行锁。

        `SKIP LOCKED` 保证多副本同时轮询时同一任务只被一个执行器领取（不重复产出）。
        """
        jobs = await self._session.scalars(
            select(AuditExportJob)
            .where(
                AuditExportJob.tenant_id == tenant_id,
                AuditExportJob.status == EXPORT_STATUS_PENDING,
                AuditExportJob.is_deleted.is_(False),
            )
            .order_by(AuditExportJob.create_time)
            .with_for_update(skip_locked=True)
        )
        return list(jobs)

    async def mark_running(self, job: AuditExportJob) -> None:
        await self._transition(job, EXPORT_STATUS_RUNNING)

    async def mark_succeeded(
        self, job: AuditExportJob, *, row_count: int, artifact_ref: str
    ) -> None:
        job.row_count = row_count
        job.artifact_ref = artifact_ref
        job.error_code = None
        await self._transition(job, EXPORT_STATUS_SUCCEEDED)

    async def mark_failed(self, job: AuditExportJob, error_code: str) -> None:
        job.error_code = error_code
        await self._transition(job, EXPORT_STATUS_FAILED)

    async def _transition(self, job: AuditExportJob, status: str) -> None:
        """状态推进：`update_time` 无 DB 侧 onupdate，显式写入使状态机在出参上可观测。"""
        job.status = status
        job.update_time = datetime.now(UTC)
        await self._session.flush()
