from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from fluxion.resources import ExecutionSnapshot
from fluxion.runtime.context import TraceEvent


@dataclass(frozen=True, slots=True)
class TraceRecord:
    trace_id: str
    execution_id: str
    tenant_id: str
    runtime_profile_id: str
    runtime_profile_version: str
    snapshot: ExecutionSnapshot
    events: tuple[TraceEvent, ...]
    latency_ms: float
    error: str | None
    model: dict[str, object] | None = None
    tools: tuple[dict[str, object], ...] = ()
    hooks: tuple[dict[str, object], ...] = ()


class TraceStore(Protocol):
    async def append(self, record: TraceRecord) -> None: ...

    async def get(self, trace_id: str) -> TraceRecord | None: ...

    async def query_by_execution(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        since: datetime,
        limit: int,
    ) -> list[TraceRecord]: ...

    async def list_recent(
        self, *, tenant_id: str, offset: int, limit: int
    ) -> tuple[list[TraceRecord], int]: ...

    async def get_by_execution(
        self, *, tenant_id: str, execution_id: str
    ) -> TraceRecord | None: ...


class InMemoryTraceStore:
    def __init__(self) -> None:
        self._records: dict[str, TraceRecord] = {}
        self._execution_index: dict[tuple[str, str], dict[str, TraceRecord]] = {}

    async def append(self, record: TraceRecord) -> None:
        previous = self._records.get(record.trace_id)
        if previous is not None:
            previous_key = (previous.tenant_id, previous.execution_id)
            self._execution_index.get(previous_key, {}).pop(record.trace_id, None)
        self._records[record.trace_id] = record
        key = (record.tenant_id, record.execution_id)
        self._execution_index.setdefault(key, {})[record.trace_id] = record

    async def get(self, trace_id: str) -> TraceRecord | None:
        return self._records.get(trace_id)

    async def query_by_execution(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        since: datetime,
        limit: int,
    ) -> list[TraceRecord]:
        if limit < 1:
            raise ValueError("limit must be positive")
        records = self._execution_index.get((tenant_id, execution_id), {}).values()
        recent = [record for record in records if record.snapshot.created_at >= since]
        recent.sort(key=lambda record: record.snapshot.created_at, reverse=True)
        return recent[:limit]

    async def list_recent(
        self,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[TraceRecord], int]:
        records = [record for record in self._records.values() if record.tenant_id == tenant_id]
        records.sort(key=lambda record: record.snapshot.created_at, reverse=True)
        return records[offset : offset + limit], len(records)

    async def get_by_execution(
        self,
        *,
        tenant_id: str,
        execution_id: str,
    ) -> TraceRecord | None:
        records = self._execution_index.get((tenant_id, execution_id), {}).values()
        return max(records, key=lambda record: record.snapshot.created_at, default=None)
