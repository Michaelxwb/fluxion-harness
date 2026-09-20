"""Run 租约续约与终态 CAS（绑定 lease owner/有效期；终态与终态事件同事务由调用方组合）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from ..infrastructure.models.runtime import Conversation, RunRecord


def current_owner_rejected(result) -> bool:
    """CAS rowcount==0 时判定为当前执行者/状态不匹配。"""
    return result.rowcount == 0


class RunLeaseService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def renew(self, run_id: uuid.UUID, owner: str, *, lease_sec: int = 60) -> bool:
        """当前 owner 续约 RUNNING 租约；其他实例/非 RUNNING 返回 False。"""
        async with self._session_factory()() as session:
            result = await session.execute(
                sa.update(RunRecord)
                .where(
                    RunRecord.id == run_id,
                    RunRecord.status == "RUNNING",
                    RunRecord.lease_owner == owner,
                )
                .values(
                    lease_until=datetime.now(UTC) + timedelta(seconds=lease_sec),
                    heartbeat_at=datetime.now(UTC),
                )
            )
            await session.commit()
            return result.rowcount == 1

    async def complete(
        self,
        run_id: uuid.UUID,
        *,
        owner: str,
        status: str,
        error_code: str | None,
        error_message: str | None,
    ) -> bool:
        """终态 CAS：仅当前 owner 且 RUNNING 时可写一次终态。"""
        async with self._session_factory()() as session:
            result = await session.execute(
                sa.update(RunRecord)
                .where(
                    RunRecord.id == run_id,
                    RunRecord.status == "RUNNING",
                    RunRecord.lease_owner == owner,
                )
                .values(
                    status=status,
                    error_code=error_code,
                    error_message=error_message,
                    end_time=datetime.now(UTC),
                )
            )
            await session.commit()
            return result.rowcount == 1
