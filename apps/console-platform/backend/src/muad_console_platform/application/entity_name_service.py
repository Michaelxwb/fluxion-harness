"""任务/定时任务列表出参的名称补齐（Worker 只回 id，名称权威源在 `control.*`）。

在**本页**收集 `agent_id/actor_user_id/skill_id` 后经 `EntityNamesRepository` 一次 IN
批查询补齐；查不到（已删/跨租户/未登记）时字段置 null，由前端回落到短 id——不编造名称，
也不逐行查询。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.entity_names_repository import (
    ACTOR,
    AGENT,
    SKILL,
    EntityNamesRepository,
)

#: 注入字段：`(id 字段, 名称字段, 权威源 kind, 是否附带 key)`。
_ENRICH_FIELDS = (
    ("agent_id", "agent_name", AGENT, False),
    ("actor_user_id", "actor_name", ACTOR, False),
    ("skill_id", "skill_name", SKILL, True),
)


def _as_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return None


class EntityNameService:
    """把 Worker 分页出参里的裸 id 补齐为名称（一条 SQL 批查询，不产生 N+1）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._repository = EntityNamesRepository(session)

    async def enrich(self, tenant_id: str, page: dict[str, Any]) -> dict[str, Any]:
        items = page.get("items")
        if not isinstance(items, list) or not items:
            return page
        wanted: set[uuid.UUID] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            for id_field, _, _, _ in _ENRICH_FIELDS:
                parsed = _as_uuid(item.get(id_field))
                if parsed is not None:
                    wanted.add(parsed)
        names = await self._repository.lookup(tenant_id, wanted)
        for item in items:
            if not isinstance(item, dict):
                continue
            for id_field, name_field, kind, with_key in _ENRICH_FIELDS:
                parsed = _as_uuid(item.get(id_field))
                entity = names.get((kind, parsed)) if parsed is not None else None
                item[name_field] = None if entity is None else entity.name
                if with_key:
                    item["skill_key"] = None if entity is None else entity.key
        return page
