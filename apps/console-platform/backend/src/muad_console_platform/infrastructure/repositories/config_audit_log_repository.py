from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.auth import ConsoleAccount
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
            select(ConfigAuditLog, ConsoleAccount.display_name)
            .outerjoin(ConsoleAccount, ConsoleAccount.id == ConfigAuditLog.actor_user_id)
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
                "actor_display_name": display_name,
                # 配置审计只在变更成功后写入，因此结果恒为 SUCCESS
                "result_status": "SUCCESS",
                "trace_id": entry.trace_id,
                "create_time": entry.create_time.isoformat(),
            }
            for entry, display_name in rows.all()
        ]

    @staticmethod
    def keyword_condition(keyword: str) -> Any:
        like = f"%{keyword}%"
        return or_(
            ConfigAuditLog.action.ilike(like),
            ConfigAuditLog.resource_type.ilike(like),
            ConfigAuditLog.trace_id.ilike(like),
        )
