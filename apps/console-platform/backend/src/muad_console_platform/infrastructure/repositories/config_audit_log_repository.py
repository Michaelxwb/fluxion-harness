from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import ConfigAuditLog


class ConfigAuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: ConfigAuditLog) -> ConfigAuditLog:
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def count(self, conditions: list[Any]) -> int:
        total = await self._session.scalar(
            select(func.count()).select_from(ConfigAuditLog).where(*conditions)
        )
        return int(total or 0)

    async def query(
        self, conditions: list[Any], page: int, page_size: int
    ) -> list[dict[str, Any]]:
        rows = await self._session.execute(
            select(ConfigAuditLog)
            .where(*conditions)
            .order_by(ConfigAuditLog.create_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return [
            {
                "id": str(entry.id),
                "resource_type": entry.resource_type,
                "resource_id": str(entry.resource_id),
                "action": entry.action,
                "actor_user_id": str(entry.actor_user_id),
                "trace_id": entry.trace_id,
                "create_time": entry.create_time.isoformat(),
            }
            for entry in rows.scalars().all()
        ]
