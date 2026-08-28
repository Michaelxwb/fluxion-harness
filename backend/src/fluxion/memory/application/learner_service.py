"""MemoryLearnerService：MemoryCandidate pipeline 正式入口（closure TASK-004）。

M208 接线：learning_enabled 从 UserPreference 读取（用户停学 → commit 拒绝）；
Policy/Consent 拒绝携带可观测 reason（不抛错）；抽取失败（None 候选）跳过不落
库。写侧仍经 MemoryLearner（gate 顺序不变），存储走真实 personal_memory 表。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.errors.console import VALIDATION_FAILED, ConsoleError
from fluxion.memory.domain.personal_memory import (
    CommitResult,
    ConsentDecision,
    MemoryCandidate,
    MemoryLearner,
    PersonalMemoryStore,
    PolicyDecision,
)
from fluxion.registry.schema import personal_memory, user_preferences


class EngineStore(Protocol):
    """暴露 engine 的 Store 门面：raw-SQL 依赖统一经 store 取 engine。

    memory 域 service 与 UserDomainService 一样收 store 根（不另传独立 engine），
    从构造上杜绝"误传异库 engine 导致 user 数据与 memory 数据静默裂分"。
    """

    engine: AsyncEngine


class MemoryLearnerService:
    """pipeline 正式入口：stop-learning gate（UserPreference）+ MemoryLearner。"""

    def __init__(self, store: EngineStore) -> None:
        self._engine = store.engine
        self._learner = MemoryLearner(store=PersonalMemoryStore(store.engine))

    async def ensure_user(self, *, tenant_id: str, platform_user_id: str) -> None:
        await self._store_ensure_user(tenant_id, platform_user_id)

    async def set_learning_enabled(self, *, tenant_id: str, platform_user_id: str, enabled: bool) -> None:
        await self._store_ensure_user(tenant_id, platform_user_id)
        async with self._engine.begin() as conn:
            await conn.execute(
                user_preferences.delete().where(
                    user_preferences.c.tenant_id == tenant_id,
                    user_preferences.c.platform_user_id == platform_user_id,
                )
            )
            await conn.execute(
                insert(user_preferences).values(
                    tenant_id=tenant_id,
                    platform_user_id=platform_user_id,
                    preference_json={"learning_enabled": enabled},
                    updated_at=datetime.now(UTC),
                )
            )

    async def commit_candidate(
        self,
        *,
        candidate: MemoryCandidate,
        policy_decision: PolicyDecision,
        consent: ConsentDecision,
    ) -> CommitResult:
        learning_enabled = await self._read_learning_enabled(
            candidate.tenant_id, candidate.user_id
        )
        return await self._learner.commit(
            candidate,
            policy_decision=policy_decision,
            consent=consent,
            learning_enabled=learning_enabled,
        )

    async def commit_batch(
        self,
        *,
        candidates: list[MemoryCandidate | None],
        policy_decision: PolicyDecision,
        consent: ConsentDecision,
    ) -> list[CommitResult]:
        """批次提交：抽取失败（None）候选跳过，不阻塞批次。"""
        results = []
        for candidate in candidates:
            if candidate is None:
                continue  # 抽取失败：跳过该候选（design §2.3.1 FEAT-P2-04）
            results.append(
                await self.commit_candidate(
                    candidate=candidate,
                    policy_decision=policy_decision,
                    consent=consent,
                )
            )
        return results

    async def list_memory(self, *, tenant_id: str, platform_user_id: str) -> list[dict[str, object]]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(
                        personal_memory.c.id,
                        personal_memory.c.memory_type,
                        personal_memory.c.content,
                    )
                    .where(
                        personal_memory.c.tenant_id == tenant_id,
                        personal_memory.c.user_id == platform_user_id,
                    )
                    .order_by(personal_memory.c.id.asc())
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    async def _read_learning_enabled(self, tenant_id: str, platform_user_id: str) -> bool:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(user_preferences.c.preference_json).where(
                        user_preferences.c.tenant_id == tenant_id,
                        user_preferences.c.platform_user_id == platform_user_id,
                    )
                )
            ).first()
        if row is None:
            return True
        payload = row[0]
        if not isinstance(payload, dict):
            return True
        return bool(payload.get("learning_enabled", True))

    async def _store_ensure_user(self, tenant_id: str, platform_user_id: str) -> None:
        # personal_memory 表不 FK platform_users；此校验为显式占位以对齐
        # users 域语义（测试走 ensure_user 保持一致性）。
        if not tenant_id.strip() or not platform_user_id.strip():
            raise ConsoleError(VALIDATION_FAILED, "tenant/user required", 400)


