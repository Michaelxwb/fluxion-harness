"""Runtime-facing v2 resolution and recall adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from muad_agent_core.memory.selection import MemoryRecordV2, QueryContextV2, QueryScopeState, select_v2
from muad_contracts.memory import MemoryCardinality, MemoryItemV2, MemoryPriority, MemoryResolution, ScopeState

from .memory_queries import MemoryCandidateRowV2, MemorySnapshotV2


def _record(row: MemoryCandidateRowV2) -> MemoryRecordV2:
    return MemoryRecordV2(
        memory_key=row.memory_key,
        memory_kind=row.memory_kind,
        predicate=row.predicate,
        scope_state=ScopeState(row.scope_state),
        scope=row.scope,
        content=row.content,
        title=row.title,
        summary=row.summary,
        tags=row.tags,
        cardinality=MemoryCardinality(row.cardinality),
        priority=MemoryPriority(row.priority),
        author=row.author,
        version=row.version,
        updated_at=row.last_confirmed_at or row.expires_at or datetime.now(UTC),
        expires_at=row.expires_at,
        enabled=row.enabled,
    )


def _item(row: MemoryCandidateRowV2) -> MemoryItemV2:
    return MemoryItemV2(
        id=row.id,
        memory_key=row.memory_key,
        memory_kind=row.memory_kind,
        predicate=row.predicate,
        scope_state=ScopeState(row.scope_state),
        scope=row.scope,
        content=row.content,
        title=row.title,
        summary=row.summary,
        tags=list(row.tags),
        cardinality=MemoryCardinality(row.cardinality),
        priority=MemoryPriority(row.priority),
        author=row.author,
        authority=row.authority,
        version=row.version,
        enabled=row.enabled,
        expires_at=row.expires_at,
        last_confirmed_at=row.last_confirmed_at,
    )


@dataclass(frozen=True, slots=True)
class MemoryV2Runtime:
    def resolve(
        self,
        snapshot: MemorySnapshotV2,
        context: QueryContextV2,
        *,
        max_items: int = 10,
    ) -> MemoryResolution:
        selected = select_v2([_record(row) for row in snapshot.rows], context, top_k=max_items)
        rows = {row.memory_key: row for row in snapshot.rows}
        entries = tuple(_item(rows[item.record.memory_key]) for item in selected)
        reason = "SCOPE_UNKNOWN" if context.scope_state is QueryScopeState.UNKNOWN else None
        return MemoryResolution(
            status="resolved" if entries else "empty",
            entries=entries,
            cache_status="miss",
            cache_revision=snapshot.revision,
            source_revision=snapshot.revision,
            truncated=False,
            error_code=None,
            clarification_reason=reason,
            retrieval_trace_id="v2-runtime",
        )

    async def forget(self, ctx, memory_id, *, expected_version: int):
        from .memory_service import MemoryService

        return await MemoryService().delete_memory(
            ctx,
            memory_id,
            expected_version=expected_version,
            command_id="v2-forget",
            endpoint="memory.delete",
        )

    def recall(
        self, snapshot: MemorySnapshotV2, context: QueryContextV2, *, limit: int = 10
    ) -> tuple[MemoryItemV2, ...]:
        selected = select_v2([_record(row) for row in snapshot.rows], context, top_k=limit)
        rows = {row.memory_key: row for row in snapshot.rows}
        return tuple(_item(rows[item.record.memory_key]) for item in selected)
