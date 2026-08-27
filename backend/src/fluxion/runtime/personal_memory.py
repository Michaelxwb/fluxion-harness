"""ADR-MEM-001：user-scoped personal memory（Episodic/Semantic）。

`personal_memory` 表 + `MemoryLearner.commit` pipeline shape（§4.7）：
MemoryCandidate → Policy gate → Consent gate → learning_enabled gate → 写入。
Phase 0 只实现 gate 判定与写入；candidate extraction / 完整策略引擎延后
Phase 2。写侧唯一入口是 `MemoryLearner.commit`——store 的写方法是私有的
`_insert`（TASK-004 E-03 architecture-test 锚点），公开面只保留用户可见的
查看/纠正/删除（NFR-PRIV-01）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.plugins.contracts import SemanticStoreProvider
from fluxion.registry.schema import personal_memory


class MemoryType(str, Enum):
    """personal memory 类型（taxonomy）：episodic 情景记忆 / semantic 语义记忆。"""

    EPISODIC = "episodic"
    SEMANTIC = "semantic"


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    """commit pipeline 的输入：一条待学习的候选，携带 provenance。"""

    tenant_id: str
    user_id: str
    memory_type: MemoryType
    content: str
    source_session_id: str
    source_range_hash: str = ""
    embedding: list[float] | None = None


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Policy gate 判定 shape（Phase 0 只携带结果；策略引擎 Phase 2）。"""

    allowed: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ConsentDecision:
    """Consent gate 判定 shape（Phase 0 只携带结果；consent 管理 Phase 2）。"""

    allowed: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CommitResult:
    committed: bool
    reason: str
    entry_id: int | None = None


@dataclass(frozen=True, slots=True)
class PersonalMemoryEntry:
    """personal_memory 行的领域投影。"""

    id: int
    tenant_id: str
    user_id: str
    memory_type: MemoryType
    content: str
    source_session_id: str
    source_range_hash: str
    created_at: datetime
    updated_at: datetime


class PersonalMemoryStore:
    """personal_memory 表访问（NFR-SEC-01：tenant_id + user_id 联合 scope 强制）。

    写入口 `_insert` 为私有方法，仅由 `MemoryLearner.commit` 调用；公开方法
    只有用户可见操作（list_entries / update_content / delete），供用户
    查看、纠正、删除自己的记忆。
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def list_entries(self, tenant_id: str, user_id: str) -> list[PersonalMemoryEntry]:
        statement = (
            select(personal_memory)
            .where(personal_memory.c.tenant_id == tenant_id)
            .where(personal_memory.c.user_id == user_id)
            .order_by(personal_memory.c.id)
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [_entry_from_row(row) for row in rows]

    async def update_content(
        self,
        tenant_id: str,
        user_id: str,
        entry_id: int,
        content: str,
    ) -> PersonalMemoryEntry | None:
        """纠正记忆内容（NFR-PRIV-01）；tenant/user scope 外的 entry 返回 None。"""
        scope = _scope(tenant_id, user_id, entry_id)
        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(personal_memory)
                .where(*scope)
                .values(content=content, updated_at=datetime.now(UTC))
            )
            if result.rowcount == 0:
                return None
            row = (
                (await connection.execute(select(personal_memory).where(*scope)))
                .mappings()
                .first()
            )
            return None if row is None else _entry_from_row(row)

    async def delete(self, tenant_id: str, user_id: str, entry_id: int) -> bool:
        """删除记忆（NFR-PRIV-01）；scope 外删除返回 False。"""
        async with self._engine.begin() as connection:
            result = await connection.execute(
                delete(personal_memory).where(*_scope(tenant_id, user_id, entry_id))
            )
            return result.rowcount > 0

    async def _insert(self, candidate: MemoryCandidate) -> PersonalMemoryEntry:
        """私有写入口：仅 MemoryLearner.commit 调用（E-03 写侧 enforcement 锚点）。"""
        now = datetime.now(UTC)
        values = {
            "tenant_id": candidate.tenant_id,
            "user_id": candidate.user_id,
            "memory_type": candidate.memory_type.value,
            "content": candidate.content,
            "embedding": candidate.embedding,
            "source_session_id": candidate.source_session_id,
            "source_range_hash": candidate.source_range_hash or None,
            # 只有通过全部 gate（含 learning_enabled）才会到这里
            "learning_enabled": True,
            "created_at": now,
            "updated_at": now,
        }
        async with self._engine.begin() as connection:
            result = await connection.execute(insert(personal_memory).values(**values))
            entry_id = int(result.inserted_primary_key[0])
        return PersonalMemoryEntry(
            id=entry_id,
            tenant_id=candidate.tenant_id,
            user_id=candidate.user_id,
            memory_type=candidate.memory_type,
            content=candidate.content,
            source_session_id=candidate.source_session_id,
            source_range_hash=candidate.source_range_hash,
            created_at=now,
            updated_at=now,
        )


class MemoryLearner:
    """personal memory 写侧唯一入口（§4.7 commit pipeline，Phase 0 shape）。

    gate 顺序：learning_enabled（user control，最优先）→ Policy → Consent；
    任一拒绝即不落库，CommitResult 携带可观测 reason。
    """

    def __init__(self, store: PersonalMemoryStore) -> None:
        self._store = store

    async def commit(
        self,
        candidate: MemoryCandidate,
        *,
        policy_decision: PolicyDecision,
        consent: ConsentDecision,
        learning_enabled: bool,
    ) -> CommitResult:
        if not learning_enabled:
            return CommitResult(committed=False, reason="learning_disabled")
        if not policy_decision.allowed:
            return CommitResult(committed=False, reason="policy_rejected")
        if not consent.allowed:
            return CommitResult(committed=False, reason="consent_rejected")
        entry = await self._store._insert(candidate)
        return CommitResult(committed=True, reason="committed", entry_id=entry.id)


class PersonalMemoryRetriever:
    """user personal memory 检索（S-04）：经 SemanticStoreProvider SPI 取 Episodic/Semantic。

    硬边界（E-01/M216 architecture test）：不得 import 或 read session 摘要
    （SessionContextSummary）——session 摘要只服务 session compaction，
    不进 user-level retrieval。本模块不依赖 SessionMemoryStore。
    """

    def __init__(self, provider: SemanticStoreProvider) -> None:
        self._provider = provider

    async def recall(
        self,
        tenant_id: str,
        user_id: str,
        query: str,
        top_k: int = 5,
        *,
        timeout_ms: int = 30_000,
    ) -> list[PersonalMemoryEntry]:
        records = await self._provider.recall(
            tenant_id, user_id, query, top_k=top_k, timeout_ms=timeout_ms
        )
        return [_entry_from_record(record) for record in records]


def _scope(tenant_id: str, user_id: str, entry_id: int) -> tuple[object, ...]:
    return (
        personal_memory.c.tenant_id == tenant_id,
        personal_memory.c.user_id == user_id,
        personal_memory.c.id == entry_id,
    )


def _entry_from_row(row: RowMapping) -> PersonalMemoryEntry:
    return PersonalMemoryEntry(
        id=int(row["id"]),
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        memory_type=MemoryType(row["memory_type"]),
        content=row["content"],
        source_session_id=row["source_session_id"],
        source_range_hash=row["source_range_hash"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _entry_from_record(record: dict[str, object]) -> PersonalMemoryEntry:
    """SemanticStore dict record → PersonalMemoryEntry（tolerant：enum/str、datetime/ISO str）。"""

    def _as_memory_type(value: object) -> MemoryType:
        return value if isinstance(value, MemoryType) else MemoryType(str(value))

    def _as_datetime(value: object) -> datetime:
        return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))

    return PersonalMemoryEntry(
        id=int(record["id"]),  # type: ignore[arg-type]
        tenant_id=str(record["tenant_id"]),
        user_id=str(record["user_id"]),
        memory_type=_as_memory_type(record["memory_type"]),
        content=str(record["content"]),
        source_session_id=str(record["source_session_id"]),
        source_range_hash=str(record.get("source_range_hash") or ""),
        created_at=_as_datetime(record["created_at"]),
        updated_at=_as_datetime(record["updated_at"]),
    )
