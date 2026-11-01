"""Run-owned named memory context; facts refresh only at the model request boundary."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from muad_agent_core.memory.components import MemoryComponent
from muad_agent_core.memory.rank import select_for_injection, score_record
from muad_agent_core.memory.render import render_memory_block
from muad_agent_core.memory.resume import resume_candidate_hash
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.canonical import canonical_json
from muad_contracts.memory import ForgetMemoryInput, MemoryCategory, MemoryResumeRef
from muad_contracts.platform_settings import MemoryPolicySettings

from ..metrics import MEMORY_INJECT_DROPPED_METRIC, MEMORY_INJECT_METRIC, record_counter
from .memory_cache import MemoryCandidateCache
from .memory_commands import MemoryCommand, MemoryCommandOutcome, MemoryMutation, MemoryWriteContext
from .memory_queries import MemorySnapshot
from .memory_service import MemoryService


@dataclass(frozen=True, slots=True)
class InjectionItem:
    memory_key: str
    version: int
    score: float
    decision: str


@dataclass(frozen=True, slots=True)
class InjectionAttribution:
    selection_hash: str
    items: tuple[InjectionItem, ...]
    dropped_count: int
    dropped_bytes: int


AttributionSink = Callable[[InjectionAttribution], Awaitable[None]]


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
                    record.memory_key
                    for record in snapshot.records
                    if record.category
                    in (
                        MemoryCategory.PROCEDURE,
                        MemoryCategory.FAILURE_PATTERN,
                        MemoryCategory.VERIFICATION,
                    )
                    and record.activation_state == "ACTIVE"
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

    def _render(self, snapshot: MemorySnapshot) -> MemoryComponent:
        ordinary = select_for_injection(
            snapshot.records,
            query=self._query,
            now=snapshot.as_of,
            inject_inferred=self._settings.inject_inferred,
            task_family=self._task_family,
            min_score=self._min_score,
            preference_items=self._preference_items,
            preference_bytes=self._preference_bytes,
            allowed_experience_keys=self._allowed_experience_keys,
        )
        selected_tasks = tuple(
            row
            for row in snapshot.records
            if self._settings.task_state_injection_enabled
            and self._task_state_ref is not None
            and row.memory_key == self._task_state_ref.memory_key
            and row.version == self._task_state_ref.expected_version
            and resume_candidate_hash(row) == self._task_state_ref.candidate_hash
            and row.category is MemoryCategory.TASK_STATE
        )
        injected = (*selected_tasks, *ordinary)
        block = render_memory_block(
            injected,
            max_records=self._settings.max_injected_memories,
            max_bytes=self._settings.max_injected_bytes,
        )
        self._pending_attribution = InjectionAttribution(
            selection_hash=hashlib.sha256(
                canonical_json([[item.memory_key, item.version] for item in injected]).encode("utf-8")
            ).hexdigest(),
            items=tuple(
                InjectionItem(
                    item.memory_key,
                    item.version,
                    score_record(item, self._query),
                    "INJECTED",
                )
                for item in injected
            ),
            dropped_count=block.dropped_count,
            dropped_bytes=block.dropped_bytes,
        )
        for reason, amount in (("count", block.dropped_count), ("bytes", block.dropped_bytes)):
            if amount:
                record_counter(MEMORY_INJECT_DROPPED_METRIC, amount, {"reason": reason})
        record_counter(MEMORY_INJECT_METRIC, labels={"outcome": "OK"})
        return MemoryComponent(snapshot_revision=snapshot.revision, block=block)

    async def forget(
        self,
        service: MemoryService,
        context: MemoryWriteContext,
        request: ForgetMemoryInput,
        *,
        command_id: str,
    ) -> MemoryCommandOutcome:
        if (context.tenant_id, context.user_id) != (self._cache.scope.tenant_id, self._cache.scope.user_id):
            raise AppError(ErrorCode.FORBIDDEN)
        if context.source_run_id is not None and context.source_run_id != self._cache.run_id:
            raise AppError(ErrorCode.FORBIDDEN)
        previously_suppressed = self._suppressed
        self.invalidate()
        self._suppressed = True
        outcome = await service.execute(
            context,
            MemoryCommand(
                command_id=command_id,
                endpoint="memory.forget",
                mutations=[
                    MemoryMutation(
                        action="DELETE",
                        memory_key=request.memory_key,
                        expected_version=request.expected_version,
                    )
                ],
            ),
        )
        if outcome.response.status != "STALE" and not previously_suppressed:
            self._suppressed = False
        return outcome
