import uuid
from collections.abc import Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

# 四个来源表的批量聚合，一次往返（UNION ALL）返回，禁止 N+1。
# agent_grant_count 必须与 API-05 的 Tab 列表同口径：JOIN agent_definition 排除已删/跨租户 Agent。
COUNTS_SQL = text(
    """
    SELECT 'agent_grant_count' AS metric, g.user_id AS row_id, count(*) AS value
      FROM control.agent_access_grant g
      JOIN control.agent_definition a ON a.id = g.agent_id
     WHERE g.is_deleted = false
       AND a.is_deleted = false
       AND a.tenant_id = :tenant_id
       AND g.user_id IN :user_ids
     GROUP BY g.user_id
    UNION ALL
    SELECT 'credential_count', c.user_id, count(*)
      FROM control.user_credential_ref c
     WHERE c.tenant_id = :tenant_id
       AND c.is_deleted = false
       AND c.user_id IN :user_ids
     GROUP BY c.user_id
    UNION ALL
    SELECT 'identity_count', i.platform_user_id, count(*)
      FROM control.channel_identity i
     WHERE i.tenant_id = :tenant_id
       AND i.is_deleted = false
       AND i.platform_user_id IN :user_ids
     GROUP BY i.platform_user_id
    UNION ALL
    SELECT 'memory_count', m.user_id, count(*)
      FROM runtime.user_memory m
     WHERE m.tenant_id = :tenant_id
       AND m.is_deleted = false
       AND m.user_id IN :user_ids
     GROUP BY m.user_id
    """
).bindparams(bindparam("user_ids", expanding=True))

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
        rows = await self._session.execute(
            COUNTS_SQL,
            {"tenant_id": tenant_id, "user_ids": list(user_ids)},
        )
        for metric, row_id, value in rows:
            if row_id in stats:
                stats[row_id][metric] = int(value)
        return stats
