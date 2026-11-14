"""Run-owned named memory context; facts refresh only at the model request boundary."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from muad_agent_core.memory.components import MemoryComponent, MemoryReference
from muad_agent_core.memory.memory_render import render_memory_v2
from muad_agent_core.memory.selection import (
    MemoryRecordV2,
    QueryContextV2,
    QueryScopeState,
    select_v2,
)
from muad_contracts.canonical import canonical_json
from muad_contracts.memory import MemoryCardinality, MemoryPriority, MemoryResumeRef, ScopeState
from muad_contracts.platform_settings import MemoryPolicySettings

from ..metrics import MEMORY_INJECT_DROPPED_METRIC, MEMORY_INJECT_METRIC, record_counter
from .memory_cache import MemoryCandidateCache
from .memory_queries import MemorySnapshotV2


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
_EXPERIENCE_KINDS = frozenset({"PROCEDURE", "FAILURE_PATTERN", "VERIFICATION"})


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

    def set_task_family(self, task_family: str | None) -> None:
        """skill 执行后由执行层更新：下一次请求按新任务族注入经验。"""
        self._task_family = task_family

    def invalidate(self) -> None:
        self._generation += 1
        self._component = None
        self._cache.invalidate()

    async def refresh(self) -> MemoryComponent | None:
        self._component = None
        if self._suppressed:
            return None
        generation = self._generation
        snapshot = await self._cache.read_for_injection(
            max_items=self._settings.max_injected_memories,
            max_bytes=self._settings.max_injected_bytes,
        )
        if snapshot is None or generation != self._generation or self._suppressed:
            return None
        if self._allowed_experience_keys is None:
            # 首次刷新冻结 ACTIVE 经验集合：之后（同 Run 内）的激活不改变本 Run 的注入集合。
            # learning 关闭时冻结为空集：不注入经验，事实记忆不受影响。
            self._allowed_experience_keys = (
                frozenset(
                    row.memory_key for row in snapshot.rows if row.memory_kind in _EXPERIENCE_KINDS
                )
                if self._learning_enabled
                else frozenset()
            )
        self._component = self._render(snapshot)
        await self._emit_attribution()
        return self._component

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

    def _render(self, snapshot: MemorySnapshotV2) -> MemoryComponent:
        records: list[MemoryRecordV2] = []
        for row in snapshot.rows:
            if row.memory_kind in _EXPERIENCE_KINDS and row.memory_key not in (self._allowed_experience_keys or ()):
                continue
            records.append(
                MemoryRecordV2(
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
                    updated_at=row.last_confirmed_at or snapshot.as_of,
                    expires_at=row.expires_at,
                    enabled=row.enabled,
                )
            )
        selected = select_v2(
            records,
            QueryContextV2(query=self._query, now=snapshot.as_of, scope_state=QueryScopeState.ABSENT),
            top_k=self._settings.max_injected_memories,
            min_score=self._min_score,
            inject_inferred=self._settings.inject_inferred,
            preference_items=self._preference_items,
        )
        rendered = render_memory_v2(
            [item.record for item in selected],
            max_records=self._settings.max_injected_memories,
            max_bytes=self._settings.max_injected_bytes,
        )
        self._pending_attribution = InjectionAttribution(
            selection_hash=hashlib.sha256(
                canonical_json([[item.record.memory_key, item.record.version] for item in selected]).encode("utf-8")
            ).hexdigest(),
            items=tuple(
                InjectionItem(item.record.memory_key, item.record.version, item.score, "SELECT", "INJECTED")
                for item in selected
            ),
            dropped_count=rendered.dropped_count,
            dropped_bytes=rendered.dropped_bytes,
            diagnostics={"allowed_experience_keys": len(self._allowed_experience_keys or ())},
        )
        if rendered.dropped_count:
            record_counter(MEMORY_INJECT_DROPPED_METRIC, rendered.dropped_count, {"reason": "count"})
        record_counter(MEMORY_INJECT_METRIC, labels={"outcome": "OK"})
        return MemoryComponent(
            snapshot_revision=snapshot.revision,
            text=rendered.text or None,
            references=tuple(MemoryReference(item.record.memory_key, item.record.version) for item in selected),
        )
