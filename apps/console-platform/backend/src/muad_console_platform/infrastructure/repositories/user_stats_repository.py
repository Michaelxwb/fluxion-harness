import uuid
from collections.abc import Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

COUNT_SQL: dict[str, str] = {
    "agent_grant_count": (
        "SELECT user_id, count(*) FROM control.agent_access_grant "
        "WHERE is_deleted = false AND user_id IN :user_ids GROUP BY user_id"
    ),
    "credential_count": (
        "SELECT user_id, count(*) FROM control.user_credential_ref "
        "WHERE tenant_id = :tenant_id AND is_deleted = false AND user_id IN :user_ids GROUP BY user_id"
    ),
    "identity_count": (
        "SELECT platform_user_id, count(*) FROM control.channel_identity "
        "WHERE tenant_id = :tenant_id AND is_deleted = false "
        "AND platform_user_id IN :user_ids GROUP BY platform_user_id"
    ),
    "memory_count": (
        "SELECT user_id, count(*) FROM runtime.user_memory "
        "WHERE tenant_id = :tenant_id AND is_deleted = false AND user_id IN :user_ids GROUP BY user_id"
    ),
}

EMPTY_COUNTS = {
    "agent_grant_count": 0,
    "credential_count": 0,
    "identity_count": 0,
    "memory_count": 0,
}


class UserStatsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def counts(self, tenant_id: str, user_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
        stats = {user_id: dict(EMPTY_COUNTS) for user_id in user_ids}
        if not user_ids:
            return stats
        for key, sql in COUNT_SQL.items():
            rows = await self._session.execute(
                text(sql).bindparams(bindparam("user_ids", expanding=True)),
                {"tenant_id": tenant_id, "user_ids": list(user_ids)},
            )
            for row_id, value in rows:
                if row_id in stats:
                    stats[row_id][key] = int(value)
        return stats
