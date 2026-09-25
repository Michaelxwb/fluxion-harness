"""审计导出任务事实与共享幂等表的读写（API-05 / RULE-09）。

任务行与幂等行由服务层在**同一事务**写入（会话提交由 `get_session` 依赖完成）；
本层只 `flush` 不提交，保证"首次提交记录 + 导出任务"原子落库。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import AuditExportJob, SkillImportIdempotency

EXPORT_STATUS_PENDING = "PENDING"


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
