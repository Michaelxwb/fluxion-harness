from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.registry.schema import session_memory
from fluxion.runtime.memory import MemoryRecord

_LEVEL_L1 = "l1"
_LEVEL_L2 = "l2"
# ADR-MEM-001：summary → SessionContextSummary 重命名（level 值随之收紧）。
# 只服务 session compaction，不进 user-level retrieval（read_l2 cross-read 已删）。
_LEVEL_SESSION_CONTEXT_SUMMARY = "session_context_summary"


class SQLSessionMemoryStore:
    """把会话记忆持久化到共享 Registry 的 SQL 后端。

    与 InMemorySessionMemoryStore 语义等价（ADR-MEM-001 taxonomy 收紧后）：
    - append_l1 / append_l2 分别写入 l1 / l2 桶；flush 只写 L1（停双写）。
    - append_summary 写入 SessionContextSummary 桶（session-scoped）；read_l1
      通过 level IN (l1, session_context_summary) 含之；read_l2 只读 level=l2，
      不再 cross-read summary（session 摘要不泄漏进 user-level retrieval）。
    - remove_l1 仅删除 l1 桶中匹配的记录，不影响 l2 / summary。

    Runtime 通过此实现把 L1/L2/SessionContextSummary 外置到
    SQLite(dev)/PostgreSQL(prod)，Pod 重启/替换后记忆不丢失，多 Pod 共享同一
    Registry 达到等价运行态。
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def append_l1(self, records: list[MemoryRecord]) -> None:
        await self._insert(records, _LEVEL_L1)

    async def append_l2(self, records: list[MemoryRecord]) -> None:
        await self._insert(records, _LEVEL_L2)

    async def append_summary(self, record: MemoryRecord) -> None:
        await self._insert([record], _LEVEL_SESSION_CONTEXT_SUMMARY)

    async def read_l1(self, tenant_id: str, session_id: str) -> list[MemoryRecord]:
        return await self._read(
            tenant_id=tenant_id,
            session_id=session_id,
            levels=(_LEVEL_L1, _LEVEL_SESSION_CONTEXT_SUMMARY),
        )

    async def read_l2(self, tenant_id: str, user_id: str) -> list[MemoryRecord]:
        # ADR-MEM-001 cross-read 修复：read_l2 只读 level=l2，不含
        # SessionContextSummary（session 摘要不泄漏进 user-level retrieval）。
        statement = (
            select(session_memory)
            .where(session_memory.c.tenant_id == tenant_id)
            .where(session_memory.c.user_id == user_id)
            .where(session_memory.c.level == _LEVEL_L2)
            .order_by(session_memory.c.id.asc())
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [_record_from_row(row) for row in rows]

    async def read_summaries(self, tenant_id: str, session_id: str) -> list[MemoryRecord]:
        statement = (
            select(session_memory)
            .where(session_memory.c.tenant_id == tenant_id)
            .where(session_memory.c.session_id == session_id)
            .where(session_memory.c.level == _LEVEL_SESSION_CONTEXT_SUMMARY)
            .order_by(session_memory.c.id.asc())
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [_record_from_row(row) for row in rows]

    async def remove_l1(self, records: list[MemoryRecord]) -> None:
        if not records:
            return
        async with self._engine.begin() as connection:
            for record in records:
                await connection.execute(
                    delete(session_memory).where(
                        session_memory.c.tenant_id == record.tenant_id,
                        session_memory.c.user_id == record.user_id,
                        session_memory.c.session_id == record.session_id,
                        session_memory.c.execution_id == record.execution_id,
                        session_memory.c.role == record.role,
                        session_memory.c.content == record.content,
                        session_memory.c.tokens == record.tokens,
                        session_memory.c.level == _LEVEL_L1,
                    )
                )

    async def _insert(self, records: list[MemoryRecord], level: str) -> None:
        if not records:
            return
        now = datetime.now(UTC)
        values = [
            {
                "tenant_id": record.tenant_id,
                "user_id": record.user_id,
                "session_id": record.session_id,
                "execution_id": record.execution_id,
                "role": record.role,
                "content": record.content,
                "tokens": record.tokens,
                "level": level,
                "created_at": now,
            }
            for record in records
        ]
        async with self._engine.begin() as connection:
            await connection.execute(insert(session_memory).values(values))

    async def _read(
        self,
        *,
        tenant_id: str,
        session_id: str,
        levels: tuple[str, ...],
    ) -> list[MemoryRecord]:
        statement = (
            select(session_memory)
            .where(session_memory.c.tenant_id == tenant_id)
            .where(session_memory.c.session_id == session_id)
            .where(session_memory.c.level.in_(levels))
            .order_by(session_memory.c.id.asc())
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [_record_from_row(row) for row in rows]


def _record_from_row(row: RowMapping) -> MemoryRecord:
    return MemoryRecord(
        tenant_id=str(row["tenant_id"]),
        user_id=str(row["user_id"]),
        session_id=str(row["session_id"]),
        execution_id=str(row["execution_id"]),
        role=str(row["role"]),
        content=str(row["content"]),
        tokens=int(row["tokens"]),
    )
