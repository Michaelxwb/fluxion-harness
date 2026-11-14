"""Run-owned named memory context: the v2 lane plus the existing legacy lane.

`MemoryContextLoader` is the single read path. PREFERENCE/FACT/NOTE come from the
v2 projection; experience and TASK_STATE keep their original lane and renderer.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from muad_agent_core.memory.components import MemoryComponent, MemoryReference
from muad_agent_core.memory.memory_render import render_memory_v2
from muad_agent_core.memory.rank import InjectionSelection, MemoryRecord, select_injection
from muad_agent_core.memory.render import RenderedMemoryBlock, render_memory_block
from muad_agent_core.memory.resume import resume_candidate_hash
from muad_agent_core.memory.selection import (
    MemoryRecordV2,
    QueryContextV2,
    QueryScopeState,
    SelectedV2,
    select_v2,
)
from muad_contracts.canonical import canonical_json
from muad_contracts.memory import (
    MemoryCardinality,
    MemoryCategory,
    MemoryItemV2,
    MemoryPriority,
    MemoryResolution,
    MemoryResumeRef,
    ScopeState,
)
from muad_contracts.platform_settings import MemoryPolicySettings

from ..metrics import MEMORY_INJECT_DROPPED_METRIC, MEMORY_INJECT_METRIC, record_counter
from .memory_cache import MemoryCandidateCache
from .memory_queries import MemoryCandidateRowV2, MemorySnapshotV2

_EXPERIENCE = frozenset(
    {MemoryCategory.PROCEDURE, MemoryCategory.FAILURE_PATTERN, MemoryCategory.VERIFICATION}
)


@dataclass(frozen=True, slots=True)
class InjectionItem:
    memory_key: str
    version: int
    score: float
    channel: str
    decision: str


@dataclass(frozen=True, slots=True)
class InjectionAttribution:
    selection_hash: str
    items: tuple[InjectionItem, ...]
    dropped_count: int
    dropped_bytes: int
    diagnostics: dict[str, object]


AttributionSink = Callable[[InjectionAttribution], Awaitable[None]]


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
        updated_at=row.last_confirmed_at,
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


class MemoryContextLoader:
    def __init__(
        self,
        cache: MemoryCandidateCache,
        *,
        settings: MemoryPolicySettings,
        query: str | None,
        task_state_ref: MemoryResumeRef | None = None,
        on_attribution: AttributionSink | None = None,
        min_score: float = 0.35,
        preference_items: int = 3,
        preference_bytes: int = 512,
        learning_enabled: bool = True,
    ) -> None:
        self._cache, self._settings = cache, settings
        self._query, self._task_state_ref = query, task_state_ref
        self._on_attribution = on_attribution
        self._allowed_experience_keys: frozenset[str] | None = None
        self._last_attribution_hash: str | None = None
        self._pending_attribution: InjectionAttribution | None = None
        self._component: MemoryComponent | None = None
        self._resolution: MemoryResolution | None = None
        self._suppressed, self._generation = False, 0
        self._task_family: str | None = None
        self._min_score = min_score
        self._preference_items = preference_items
        self._preference_bytes = preference_bytes
        self._learning_enabled = learning_enabled

    @property
    def cache(self) -> MemoryCandidateCache:
        return self._cache

    @property
    def query(self) -> str | None:
        return self._query

    @property
    def component(self) -> MemoryComponent | None:
        return self._component

    @property
    def resolution(self) -> MemoryResolution | None:
        """§3.4.6 view of the last refresh, limited to the v2 lane."""

        return self._resolution

    def set_task_family(self, task_family: str | None) -> None:
        """skill 执行后由执行层更新：下一次请求按新任务族注入经验。"""
        self._task_family = task_family

    def invalidate(self) -> None:
        self._generation += 1
        self._component = None
        self._resolution = None
        self._cache.invalidate()

    @property
    def suppressed(self) -> bool:
        return self._suppressed

    def begin_forget(self) -> bool:
        """A forget attempt hides memory for the rest of the Run until it succeeds."""

        previous = self._suppressed
        self.invalidate()
        self._suppressed = True
        return previous

    def end_forget(self, previous: bool, *, succeeded: bool) -> None:
        if succeeded:
            self._suppressed = previous

    async def refresh(self) -> MemoryComponent | None:
        self._component = None
        self._resolution = None
        if self._suppressed:
            return None
        generation = self._generation
        snapshot = await self._cache.read_for_injection(
            max_items=self._settings.max_injected_memories,
            max_bytes=self._settings.max_injected_bytes,
        )
        if snapshot is None:
            # best_effort: no injection, but the read failure stays visible.
            self._resolution = MemoryResolution(
                status="degraded",
                entries=(),
                cache_status="unavailable",
                cache_revision=None,
                source_revision=0,
                truncated=False,
                error_code="MEMORY_READ_FAILED",
                clarification_reason=None,
                retrieval_trace_id="memory-context",
            )
            return None
        if generation != self._generation or self._suppressed:
            return None
        self._freeze_experience(snapshot)
        selection = select_injection(
            (row.record for row in snapshot.legacy_rows),
            query=self._query,
            now=snapshot.as_of,
            inject_inferred=self._settings.inject_inferred,
            task_family=self._task_family,
            min_score=self._min_score,
            preference_items=self._preference_items,
            preference_bytes=self._preference_bytes,
            allowed_experience_keys=self._allowed_experience_keys,
        )
        tasks = self._task_state(snapshot)
        selected = select_v2(
            [_record(row) for row in snapshot.rows],
            QueryContextV2(query=self._query, now=snapshot.as_of, scope_state=QueryScopeState.ABSENT),
            top_k=self._settings.max_injected_memories,
            min_score=self._min_score,
            inject_inferred=self._settings.inject_inferred,
            preference_items=self._preference_items,
        )
        self._component = self._render(snapshot, selection, tasks, selected)
        self._resolution = self._resolve(snapshot, selected)
        await self._emit_attribution()
        return self._component

    def _freeze_experience(self, snapshot: MemorySnapshotV2) -> None:
        if self._allowed_experience_keys is not None:
            return
        # 首次刷新冻结 ACTIVE 经验集合：之后（同 Run 内）的激活不改变本 Run 的注入集合。
        self._allowed_experience_keys = (
            frozenset(
                row.record.memory_key
                for row in snapshot.legacy_rows
                if row.record.category in _EXPERIENCE and row.record.activation_state == "ACTIVE"
            )
            if self._learning_enabled
            else frozenset()
        )

    def _task_state(self, snapshot: MemorySnapshotV2) -> tuple[MemoryRecord, ...]:
        ref = self._task_state_ref
        if ref is None or not self._settings.task_state_injection_enabled:
            return ()
        return tuple(
            row.record
            for row in snapshot.legacy_rows
            if row.record.category is MemoryCategory.TASK_STATE
            and row.record.memory_key == ref.memory_key
            and row.record.version == ref.expected_version
            and resume_candidate_hash(row.record) == ref.candidate_hash
        )

    def _render(
        self,
        snapshot: MemorySnapshotV2,
        selection: InjectionSelection,
        tasks: tuple[MemoryRecord, ...],
        selected: tuple[SelectedV2, ...],
    ) -> MemoryComponent:
        max_records, max_bytes = (
            self._settings.max_injected_memories,
            self._settings.max_injected_bytes,
        )
        legacy = (*tasks, *selection.records)
        block: RenderedMemoryBlock = render_memory_block(
            legacy, max_records=max_records, max_bytes=max_bytes
        )
        block_text = block.text
        rendered = render_memory_v2(
            [item.record for item in selected],
            max_records=max(0, max_records - len(block.items)),
            max_bytes=max(0, max_bytes - len(block_text.encode("utf-8") if block_text else b"")),
            notice=block_text is None,
        )
        v2_text = rendered.text if rendered.included_keys else None
        text = "\n".join(part for part in (block_text, v2_text) if part)
        meta = {item.record.memory_key: (item.score, item.channel) for item in selection.items}
        self._pending_attribution = InjectionAttribution(
            selection_hash=hashlib.sha256(
                canonical_json(
                    [
                        [record.memory_key, record.version]
                        for record in (*legacy, *(item.record for item in selected))
                    ]
                ).encode("utf-8")
            ).hexdigest(),
            items=tuple(
                InjectionItem(
                    record.memory_key,
                    record.version,
                    *meta.get(record.memory_key, (1.0, "TASK_STATE")),
                    "INJECTED",
                )
                for record in legacy
            )
            + tuple(
                InjectionItem(item.record.memory_key, item.record.version, item.score, "V2", "INJECTED")
                for item in selected
            ),
            dropped_count=block.dropped_count + rendered.dropped_count,
            dropped_bytes=block.dropped_bytes + rendered.dropped_bytes,
            diagnostics={
                "learning_enabled": self._learning_enabled,
                "min_score": self._min_score,
                "allowed_experience_keys": len(self._allowed_experience_keys or ()),
                "task_state": len(tasks),
                "skipped": [
                    {
                        "memory_key": item.memory_key,
                        "activation_state": item.activation_state,
                        "reason": item.reason,
                        "score": item.score,
                    }
                    for item in selection.skipped[:20]
                ],
            },
        )
        for reason, amount in (
            ("count", self._pending_attribution.dropped_count),
            ("bytes", self._pending_attribution.dropped_bytes),
        ):
            if amount:
                record_counter(MEMORY_INJECT_DROPPED_METRIC, amount, {"reason": reason})
        record_counter(MEMORY_INJECT_METRIC, labels={"outcome": "OK"})
        return MemoryComponent(
            snapshot_revision=snapshot.revision,
            text=text or None,
            block=block,
            references=tuple(
                MemoryReference(item.record.memory_key, item.record.version, item.omitted)
                for item in block.items
            )
            + tuple(
                MemoryReference(item.record.memory_key, item.record.version)
                for item in selected
            ),
        )

    def _resolve(
        self, snapshot: MemorySnapshotV2, selected: tuple[SelectedV2, ...]
    ) -> MemoryResolution:
        rows = {row.memory_key: row for row in snapshot.rows}
        entries = tuple(_item(rows[item.record.memory_key]) for item in selected)
        reason = None
        return MemoryResolution(
            status="resolved" if entries else "empty",
            entries=entries,
            cache_status="miss",
            cache_revision=snapshot.revision,
            source_revision=snapshot.revision,
            truncated=False,
            error_code=None,
            clarification_reason=reason,
            retrieval_trace_id="memory-context",
        )

    async def _emit_attribution(self) -> None:
        attribution = self._pending_attribution
        self._pending_attribution = None
        if (
            attribution is None
            or self._on_attribution is None
            or attribution.selection_hash == self._last_attribution_hash
        ):
            return
        self._last_attribution_hash = attribution.selection_hash
        await self._on_attribution(attribution)
