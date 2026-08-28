"""MemoryUserService：用户侧 Personal Memory 操作（closure TASK-005）。

组合 PersonalMemoryStore（list/update_content/delete）+ PgVectorSemanticStore
（纠正后 embedding 重算回写）+ 缓存失效钩子（cache-aside：先写库再失效，
key = `fluxion:mem:{tenant}:{user}:{type}`，design §3.3）。reindex 不存在的
条目 → KeyError（明确错误，不静默）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.engine import RowMapping

from fluxion.memory.application.learner_service import EngineStore
from fluxion.memory.domain.personal_memory import (
    MemoryCandidate,
    MemoryLearner,
    MemoryType,
    PersonalMemoryEntry,
    PersonalMemoryStore,
)
from fluxion.plugins.providers.pgvector_semantic import PgVectorSemanticStore


def _cache_key(tenant_id: str, user_id: str, memory_type: str) -> str:
    return f"fluxion:mem:{tenant_id}:{user_id}:{memory_type}"


class MemoryUserService:
    """用户侧查看/纠正/删除 + reindex（NFR-PRIV-01 后端契约）。"""

    def __init__(self, store: EngineStore) -> None:
        self._engine = store.engine
        self._store = PersonalMemoryStore(store.engine)
        self._semantic = PgVectorSemanticStore(store.engine)
        self._learner = MemoryLearner(store=self._store)

    async def commit_candidate(
        self,
        *,
        candidate: MemoryCandidate,
        policy_decision: Any,
        consent: Any,
    ) -> Any:
        return await self._learner.commit(
            candidate,
            policy_decision=policy_decision,
            consent=consent,
            learning_enabled=True,
        )

    async def list_entries(self, *, tenant_id: str, user_id: str) -> list[PersonalMemoryEntry]:
        return await self._store.list_entries(tenant_id, user_id)

    async def correct(
        self,
        *,
        tenant_id: str,
        user_id: str,
        entry_id: int,
        content: str,
        on_cache_invalidate: Callable[[str], None] | None = None,
    ) -> PersonalMemoryEntry | None:
        """纠正：更新内容 + embedding 重算回写 + 缓存失效（先写库后失效）。"""
        updated = await self._store.update_content(tenant_id, user_id, entry_id, content)
        if updated is None:
            return None
        embedding = self._recompute_embedding(content)
        await self._rewrite_embedding(tenant_id, user_id, entry_id, embedding)
        if on_cache_invalidate is not None:
            on_cache_invalidate(_cache_key(tenant_id, user_id, updated.memory_type.value))
        return updated

    async def delete(
        self,
        *,
        tenant_id: str,
        user_id: str,
        entry_id: int,
        memory_type: MemoryType,
        on_cache_invalidate: Callable[[str], None] | None = None,
    ) -> bool:
        """删除：移除条目 + 缓存失效（先写库后失效）。"""
        deleted = await self._store.delete(tenant_id, user_id, entry_id)
        if deleted and on_cache_invalidate is not None:
            on_cache_invalidate(_cache_key(tenant_id, user_id, memory_type.value))
        return deleted

    async def reindex(self, *, tenant_id: str, user_id: str, entry_id: int) -> None:
        """重算指定条目的 embedding（reindex 语义入口）。"""
        entry = await self._get_entry(tenant_id, user_id, entry_id)
        if entry is None:
            raise KeyError(f"personal memory entry not found: {entry_id}")
        embedding = self._recompute_embedding(entry.content)
        await self._rewrite_embedding(tenant_id, user_id, entry_id, embedding)

    # ---- 内部 ---------------------------------------------------------------

    def _recompute_embedding(self, content: str) -> list[float]:
        """确定性 embedding 重算（词级 hash 投影；生产接模型 provider，接口不变）。"""
        vec = [0.0] * 16
        for token in content.split():
            vec[hash(token) % 16] += 1.0
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]

    async def _get_entry(self, tenant_id: str, user_id: str, entry_id: int) -> RowMapping | None:
        from fluxion.registry.schema import personal_memory

        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(personal_memory).where(
                        personal_memory.c.tenant_id == tenant_id,
                        personal_memory.c.user_id == user_id,
                        personal_memory.c.id == entry_id,
                    )
                )
            ).mappings().first()
        if row is None:
            return None
        return row

    async def _rewrite_embedding(
        self, tenant_id: str, user_id: str, entry_id: int, embedding: list[float]
    ) -> None:
        from fluxion.registry.schema import personal_memory

        async with self._engine.begin() as conn:
            await conn.execute(
                update(personal_memory)
                .where(
                    personal_memory.c.tenant_id == tenant_id,
                    personal_memory.c.user_id == user_id,
                    personal_memory.c.id == entry_id,
                )
                .values(embedding=embedding, updated_at=datetime.now(UTC))
            )
