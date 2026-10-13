"""Run-owned named memory context; facts refresh only at the model request boundary."""

from __future__ import annotations

from muad_agent_core.memory.components import MemoryComponent
from muad_agent_core.memory.rank import select_for_injection
from muad_agent_core.memory.render import render_memory_block
from muad_agent_core.memory.resume import resume_candidate_hash
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import ForgetMemoryInput, MemoryCategory, MemoryResumeRef
from muad_contracts.platform_settings import MemoryPolicySettings

from ..metrics import MEMORY_INJECT_DROPPED_METRIC, MEMORY_INJECT_METRIC, record_counter
from .memory_cache import MemoryCandidateCache
from .memory_commands import MemoryCommand, MemoryCommandOutcome, MemoryMutation, MemoryWriteContext
from .memory_queries import MemorySnapshot
from .memory_service import MemoryService


class MemoryContextLoader:
    def __init__(
        self,
        cache: MemoryCandidateCache,
        *,
        settings: MemoryPolicySettings,
        query: str | None,
        task_state_ref: MemoryResumeRef | None = None,
    ) -> None:
        self._cache, self._settings = cache, settings
        self._query, self._task_state_ref = query, task_state_ref
        self._component: MemoryComponent | None = None
        self._suppressed, self._generation = False, 0

    @property
    def cache(self) -> MemoryCandidateCache:
        return self._cache

    @property
    def query(self) -> str | None:
        return self._query

    @property
    def component(self) -> MemoryComponent | None:
        return self._component

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
        self._component = self._render(snapshot)
        return self._component

    def _render(self, snapshot: MemorySnapshot) -> MemoryComponent:
        ordinary = select_for_injection(
            snapshot.records,
            query=self._query,
            now=snapshot.as_of,
            inject_inferred=self._settings.inject_inferred,
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
        block = render_memory_block(
            (*selected_tasks, *ordinary),
            max_records=self._settings.max_injected_memories,
            max_bytes=self._settings.max_injected_bytes,
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
