from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import ConfigAuditLog


class ConfigAuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: ConfigAuditLog) -> ConfigAuditLog:
        self._session.add(entry)
        await self._session.flush()
        return entry
