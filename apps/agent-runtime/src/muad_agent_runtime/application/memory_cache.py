"""Run-owned candidate cache; revision/time checks happen at every injection read."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.exc import SQLAlchemyError

from ..metrics import MEMORY_INJECT_METRIC, record_counter
from .memory_queries import MemoryCandidateRow, MemoryQueries, MemoryReadScope, MemorySnapshot

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _CachedCandidates:
    revision: int
    rows: tuple[MemoryCandidateRow, ...]


class MemoryCandidateCache:
    def __init__(self, queries: MemoryQueries, *, scope: MemoryReadScope, run_id: UUID) -> None:
        self._queries = queries
        self._scope = scope
        self._run_id = run_id
        self._cached: _CachedCandidates | None = None
        self._lock = asyncio.Lock()

    @property
    def scope(self) -> MemoryReadScope:
        return self._scope

    @property
    def run_id(self) -> UUID:
        return self._run_id

    @property
    def cached_revision(self) -> int | None:
        return self._cached.revision if self._cached is not None else None

    def invalidate(self) -> None:
        self._cached = None

    async def read_for_injection(self, *, max_items: int, max_bytes: int) -> MemorySnapshot | None:
        if max_items < 0 or max_bytes < 0:
            raise ValueError("memory injection budgets must be non-negative")
        async with self._lock:
            if max_items == 0 or max_bytes == 0:
                self.invalidate()
                return None
            try:
                return await self._read_current()
            except (SQLAlchemyError, AppError, ValueError) as exc:
                self.invalidate()
                record_counter(MEMORY_INJECT_METRIC, labels={"outcome": "ERROR"})
                # Exception text may contain persisted content, quote or bound SQL parameters.
                logger.warning(
                    "memory_snapshot_read_failed",
                    extra={
                        "tenant_id": self._scope.tenant_id,
                        "user_id": str(self._scope.user_id),
                        "run_id": str(self._run_id),
                        "error_code": ErrorCode.MEMORY_READ_FAILED.value,
                        "error_type": type(exc).__name__,
                    },
                )
                return None

    async def _read_current(self) -> MemorySnapshot:
        cached = self._cached
        if cached is not None:
            head = await self._queries.read_scope_version(self._scope)
            if head.revision == cached.revision:
                rows = tuple(
                    row
                    for row in cached.rows
                    if row.record.enabled
                    and not row.record.is_deleted
                    and (row.record.expires_at is None or row.record.expires_at > head.as_of)
                )
                return MemorySnapshot(self._scope, head.revision, head.forget_epoch, head.as_of, rows)
        snapshot = await self._queries.read_snapshot(self._scope)
        self._cached = _CachedCandidates(snapshot.revision, snapshot.rows)
        return snapshot
