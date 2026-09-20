"""受控长期 Memory：PREFERENCE/WORK_STYLE/EXPLICIT 的版本化读写（runtime.user_memory）。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import get_session_factory
from ..infrastructure.models.runtime import UserMemory

ALLOWED_CATEGORIES = frozenset({"PREFERENCE", "WORK_STYLE", "EXPLICIT"})


class MemoryService:
    """跨进程边界使用；每次操作持有独立短事务。"""

    async def upsert(
        self,
        *,
        tenant_id: str,
        user_id: uuid.UUID,
        category: str,
        memory_key: str,
        content_json: dict[str, Any],
        source_type: str,
        source_ref: str | None = None,
    ) -> dict[str, Any]:
        if category not in ALLOWED_CATEGORIES:
            raise ValueError(f"memory category not allowed: {category}")
        async with get_session_factory()() as session:
            row = (
                await session.execute(
                    select(UserMemory).where(
                        UserMemory.tenant_id == tenant_id,
                        UserMemory.user_id == user_id,
                        UserMemory.memory_key == memory_key,
                        UserMemory.is_deleted.is_(False),
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = UserMemory(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    memory_key=memory_key,
                    category=category,
                    content_json=content_json,
                    source_type=source_type,
                    source_ref=source_ref,
                    version=1,
                )
                session.add(row)
                await session.flush()
            else:
                row.category = category
                row.content_json = content_json
                row.source_type = source_type
                row.source_ref = source_ref
                row.version += 1
                row.update_time = row.update_time  # touch
            await session.commit()
            return self._snapshot(row)

    async def list_entries(self, tenant_id: str, user_id: uuid.UUID) -> list[dict[str, Any]]:
        async with get_session_factory()() as session:
            rows = (
                await session.execute(
                    select(UserMemory)
                    .where(
                        UserMemory.tenant_id == tenant_id,
                        UserMemory.user_id == user_id,
                        UserMemory.enabled.is_(True),
                        UserMemory.is_deleted.is_(False),
                    )
                    .order_by(UserMemory.memory_key)
                )
            ).scalars().all()
            return [self._snapshot(row) for row in rows]

    async def disable(self, tenant_id: str, user_id: uuid.UUID, memory_key: str) -> None:
        """软删除（is_deleted=true）：历史可审计，运行时不再读取。"""
        async with get_session_factory()() as session:
            row = (
                await session.execute(
                    select(UserMemory).where(
                        UserMemory.tenant_id == tenant_id,
                        UserMemory.user_id == user_id,
                        UserMemory.memory_key == memory_key,
                        UserMemory.is_deleted.is_(False),
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                row.is_deleted = True
                await session.commit()

    @staticmethod
    def _snapshot(row: UserMemory) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "user_id": str(row.user_id),
            "memory_key": row.memory_key,
            "category": row.category,
            "content_json": row.content_json,
            "source_type": row.source_type,
            "source_ref": row.source_ref,
            "version": row.version,
            "enabled": row.enabled,
        }
