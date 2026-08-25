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
    def __init__(self, *, max_records: int = 10_000) -> None:
        self._records: dict[str, TraceRecord] = {}
        self._execution_index: dict[tuple[str, str], dict[str, TraceRecord]] = {}
        # 长跑进程内存上限：此前 _records 无界增长，dev/in-memory 部署下 OOM。
        self._max_records = max_records

    async def append(self, record: TraceRecord) -> None:
        previous = self._records.get(record.trace_id)
        if previous is not None:
            previous_key = (previous.tenant_id, previous.execution_id)
            self._execution_index.get(previous_key, {}).pop(record.trace_id, None)
        self._records[record.trace_id] = record
        key = (record.tenant_id, record.execution_id)
        self._execution_index.setdefault(key, {})[record.trace_id] = record
        self._trim()

    def _trim(self) -> None:
        """超过容量上限时按 created_at 淘汰最旧记录，防止长跑进程内存无界增长。"""
        if len(self._records) <= self._max_records:
            return
        sorted_ids = sorted(
            self._records, key=lambda tid: self._records[tid].snapshot.created_at
        )
        for tid in sorted_ids[: len(self._records) - self._max_records]:
            record = self._records.pop(tid, None)
            if record is not None:
                key = (record.tenant_id, record.execution_id)
                self._execution_index.get(key, {}).pop(tid, None)

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
