from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.auth import ConsoleSession


class ConsoleSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_token_hash(self, token_hash: str) -> ConsoleSession | None:
        row: ConsoleSession | None = await self._session.scalar(
            select(ConsoleSession).where(
                ConsoleSession.token_hash == token_hash,
                ConsoleSession.is_deleted.is_(False),
            )
        )
        return row

    async def add(self, session_row: ConsoleSession) -> ConsoleSession:
        self._session.add(session_row)
        await self._session.flush()
        return session_row
