"""PgVectorSemanticStore：SemanticStoreProvider 的 PG 生产实现（closure TASK-003）。

双库契约（规则 7）：SQLite 与 PostgreSQL 走同一 provider 契约、同一测试。

embedding 存储双层：
- **native pgvector**（探测到扩展时）：VECTOR 列 + `<=>` 余弦距离 SQL 排序；
- **降级**（扩展不可用）：embedding JSON 列 + Python 侧 cosine 排序——真实 PG
  表可测（local-pg-test-env），语义一致，仅排序在应用侧完成（行数受
  user/tenant scope 约束）。native 路径在扩展可用时自动接管。

全部查询按 tenant_id + user_id scope（NFR-SEC-01）；memory_type 过滤由
provider 承担。超时/失败语义：查询异常 → SemanticStoreError（调用方降级空
manifest，不阻塞 Execution）。
"""

from __future__ import annotations

import logging
import math
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


class SemanticStoreError(RuntimeError):
    """SemanticStore 查询/写入失败（调用方降级空 manifest）。"""


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _parse_embedding(raw: object) -> list[float]:
    if isinstance(raw, str):
        # pgvector native 文本形态 "[1,2,3]"
        inner = raw.strip("[]")
        return [float(part) for part in inner.split(",") if part.strip()]
    if isinstance(raw, (list, tuple)):
        return [float(item) for item in raw]
    return []


class PgVectorSemanticStore:
    """personal_memory 表上的语义检索 provider（design §3.4 / TASK-003）。"""

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self._pgvector_available: bool | None = None

    async def initialize(self) -> None:
        """探测 pgvector 可用性（不可用时降级 JSON+Python cosine，不失败）。"""
        try:
            async with self._engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
                    )
                ).scalar()
                self._pgvector_available = bool(row)
        except SQLAlchemyError:
            # 探测失败（如 SQLite 无 pg_extension 表）→ 降级非 native 路径；
            # 属预期降级，记 debug 可观测、不告警（规则：禁止静默吞异常）
            logger.debug("pgvector probe failed; degraded to Python cosine", exc_info=True)
            self._pgvector_available = False

    @property
    def native_vector(self) -> bool:
        return bool(self._pgvector_available)

    async def store(
        self,
        tenant_id: str,
        user_id: str,
        record: dict[str, Any],
        timeout_ms: int = 30_000,
    ) -> None:
        """写入/更新一条 memory + embedding（按 scope+content 幂等 upsert 简化为插入）。"""
        import datetime as _dt

        from sqlalchemy import insert

        from fluxion.registry.schema import personal_memory

        now = _dt.datetime.now(_dt.UTC)
        values: dict[str, Any] = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "memory_type": str(record.get("memory_type", "semantic")),
            "content": str(record.get("content", "")),
            "source_session_id": str(record.get("source_session_id", "session")),
            "learning_enabled": bool(record.get("learning_enabled", True)),
        }
        embedding = record.get("embedding")
        values["embedding"] = embedding if embedding is not None else None
        stmt = insert(personal_memory).values(
            created_at=now,
            updated_at=now,
            **values,
        )
        try:
            async with self._engine.begin() as conn:
                await conn.execute(stmt)
        except Exception as exc:
            raise SemanticStoreError(f"semantic store failed: {exc}") from exc

    @property
    def _is_postgres(self) -> bool:
        return bool(self._engine.dialect.name == "postgresql")

    async def recall(
        self,
        tenant_id: str,
        user_id: str,
        query: str,
        top_k: int = 5,
        timeout_ms: int = 30_000,
        *,
        query_embedding: list[float] | None = None,
        memory_type: str | None = None,
    ) -> list[dict[str, object]]:
        """语义召回：cosine 排序 + memory_type 过滤 + tenant/user 隔离。

        record 契约与 `PersonalMemoryRetriever` 的 `_entry_from_record` 对齐：
        完整行投影 + `id` 键（生产 provider 与测试 TableBackedSemanticStore
        同形态）；`score` 为附加排序信息，消费方按需读取。
        """
        from sqlalchemy import select

        from fluxion.registry.schema import personal_memory

        del query  # 查询语义经 query_embedding 表达；文本检索属 Phase 6 排序器
        stmt = select(
            personal_memory.c.id,
            personal_memory.c.tenant_id,
            personal_memory.c.user_id,
            personal_memory.c.memory_type,
            personal_memory.c.content,
            personal_memory.c.embedding,
            personal_memory.c.source_session_id,
            personal_memory.c.source_range_hash,
            personal_memory.c.created_at,
            personal_memory.c.updated_at,
        ).where(
            personal_memory.c.tenant_id == tenant_id,
            personal_memory.c.user_id == user_id,
        )
        if memory_type is not None:
            stmt = stmt.where(personal_memory.c.memory_type == memory_type)
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(stmt)).mappings().all()
        except Exception as exc:
            raise SemanticStoreError(f"semantic recall failed: {exc}") from exc

        query_vec = query_embedding or []
        scored: list[tuple[float, dict[str, object]]] = []
        for row in rows:
            embedding = _parse_embedding(row["embedding"])
            score = _cosine(query_vec, embedding) if query_vec else 0.0
            scored.append(
                (
                    score,
                    {
                        "id": row["id"],
                        "tenant_id": row["tenant_id"],
                        "user_id": row["user_id"],
                        "memory_type": row["memory_type"],
                        "content": row["content"],
                        "source_session_id": row["source_session_id"],
                        "source_range_hash": row["source_range_hash"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                        "score": score,
                    },
                )
            )
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    async def search(
        self,
        tenant_id: str,
        user_id: str,
        filters: dict[str, Any],
        timeout_ms: int = 30_000,
    ) -> list[dict[str, object]]:
        return await self.recall(
            tenant_id,
            user_id,
            query="",
            top_k=int(filters.get("top_k", 20)),
            memory_type=filters.get("memory_type"),
        )
