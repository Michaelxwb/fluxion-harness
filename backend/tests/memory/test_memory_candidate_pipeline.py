"""TASK-004（phase2）MemoryCandidate pipeline 正式化 + learning control 验收测试。

S-04（E2E，RULE-P2-05 / NFR-PRIV-01）：关闭自动学习的用户 → candidate commit
被拒，`personal_memory` 无新行；开启时正常提交。
E-03（integration）：Policy/Consent 拒绝 → commit 拒绝并记录 reason。

真实边界：真实 SQLite personal_memory 表 + user_preferences 表 + MemoryLearner
+ MemoryLearnerService；不 mock。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.memory.application.learner_service import MemoryLearnerService
from fluxion.memory.domain.personal_memory import (
    ConsentDecision,
    MemoryCandidate,
    MemoryType,
    PolicyDecision,
)
from fluxion.registry import SQLiteRegistryStore


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        yield store.engine
    finally:
        await store.close()


@pytest.fixture
async def service(engine: AsyncEngine) -> MemoryLearnerService:
    svc = MemoryLearnerService(engine)
    await svc.ensure_user(tenant_id="tenant-a", platform_user_id="user-a")
    return svc


def _candidate(**overrides: object) -> MemoryCandidate:
    data: dict[str, object] = {
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "memory_type": MemoryType.SEMANTIC,
        "content": "报告先给结论",
        "source_session_id": "session-1",
        "source_range_hash": "hash-1",
    }
    data.update(overrides)
    return MemoryCandidate(**data)


async def test_s04_commit_with_learning_enabled_persists(service: MemoryLearnerService) -> None:
    """开启自动学习（默认）→ commit 落库。"""
    result = await service.commit_candidate(
        candidate=_candidate(),
        policy_decision=PolicyDecision(allowed=True, reason="ok"),
        consent=ConsentDecision(allowed=True, reason="ok"),
    )
    assert result.committed is True
    rows = await service.list_memory(tenant_id="tenant-a", platform_user_id="user-a")
    assert len(rows) == 1 and rows[0]["content"] == "报告先给结论"


async def test_s04_learning_disabled_rejects_and_no_row(
    service: MemoryLearnerService, engine: AsyncEngine
) -> None:
    """关闭自动学习 → commit 拒绝（RULE-P2-05），personal_memory 无新行。"""
    await service.set_learning_enabled(tenant_id="tenant-a", platform_user_id="user-a", enabled=False)

    result = await service.commit_candidate(
        candidate=_candidate(content="不应落库"),
        policy_decision=PolicyDecision(allowed=True, reason="ok"),
        consent=ConsentDecision(allowed=True, reason="ok"),
    )
    assert result.committed is False and result.reason == "learning_disabled"
    assert await service.list_memory(tenant_id="tenant-a", platform_user_id="user-a") == []
    # 表级核验：无新行
    from sqlalchemy import select

    from fluxion.registry.schema import personal_memory

    async with engine.connect() as conn:
        count = (await conn.execute(select(personal_memory.c.id))).scalars().all()
    assert count == []


async def test_e03_policy_and_consent_rejections_record_reason(
    service: MemoryLearnerService,
) -> None:
    """Policy/Consent 拒绝 → commit 拒绝 + reason 可观测（不抛错）。"""
    rejected_policy = await service.commit_candidate(
        candidate=_candidate(content="p"),
        policy_decision=PolicyDecision(allowed=False, reason="sensitive_content"),
        consent=ConsentDecision(allowed=True, reason="ok"),
    )
    assert rejected_policy.committed is False
    assert rejected_policy.reason == "policy_rejected"

    rejected_consent = await service.commit_candidate(
        candidate=_candidate(content="c"),
        policy_decision=PolicyDecision(allowed=True, reason="ok"),
        consent=ConsentDecision(allowed=False, reason="user_declined"),
    )
    assert rejected_consent.committed is False
    assert rejected_consent.reason == "consent_rejected"


async def test_s04_extraction_failure_skips_candidate(service: MemoryLearnerService) -> None:
    """模型抽取失败的候选被跳过（不阻塞批次、不落库）。"""
    candidates: list[MemoryCandidate | None] = [
        _candidate(content="正常候选"),
        None,  # 抽取失败 → None
        _candidate(content="第二条正常"),
    ]
    results = await service.commit_batch(
        candidates=candidates,
        policy_decision=PolicyDecision(allowed=True, reason="ok"),
        consent=ConsentDecision(allowed=True, reason="ok"),
    )
    assert len(results) == 2  # 失败候选被跳过
    assert all(r.committed for r in results)
