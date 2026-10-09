"""Console 列表出参的名称补齐读取（`control.*` 权威源）。

Worker Admin API 只回 id；Agent/执行用户/Skill 的名称权威源在 Console 自己的 `control`
schema（`agent_definition` / `platform_user` / `skill`）。本仓库按**本页**收集到的 id 一次
IN 批查询取回全部名称——UUID 跨表不互通，各 UNION 分支只命中自己的表，一条 SQL、一次往返，
不做逐行查询（与 `user_stats_repository` / `audit_query_repository` 同口径）。
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

AGENT = "AGENT"
ACTOR = "ACTOR"
SKILL = "SKILL"

#: 一个 IN 列表服务三个分支：id 不在某分支的表里时该分支自然不命中。
_LOOKUP_SQL = text(
    """
    SELECT 'AGENT' AS kind, a.id AS id, a.name AS entity_name, a.key AS entity_key
      FROM control.agent_definition a
     WHERE a.tenant_id = :tenant_id
       AND a.is_deleted = false
       AND a.id IN :ids
    UNION ALL
    SELECT 'ACTOR', u.id, u.display_name, NULL
      FROM control.platform_user u
     WHERE u.tenant_id = :tenant_id
       AND u.is_deleted = false
       AND u.id IN :ids
    UNION ALL
    SELECT 'SKILL', s.id, s.name, s.key
      FROM control.skill s
     WHERE s.tenant_id = :tenant_id
       AND s.is_deleted = false
       AND s.id IN :ids
    """
).bindparams(bindparam("ids", expanding=True))


@dataclass(frozen=True, slots=True)
class EntityName:
    """一个实体的可读名称；`key` 仅 Agent/Skill 有，执行用户为 None。"""

    name: str
    key: str | None


class EntityNamesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lookup(
        self, tenant_id: str, ids: Collection[uuid.UUID]
    ) -> dict[tuple[str, uuid.UUID], EntityName]:
        """按 `(kind, id)` 返回名称；空 id 集合不发查询。"""
        if not ids:
            return {}
        rows = await self._session.execute(
            _LOOKUP_SQL, {"tenant_id": tenant_id, "ids": list(ids)}
        )
        return {
            (str(row.kind), row.id): EntityName(name=str(row.entity_name), key=row.entity_key)
            for row in rows
        }
