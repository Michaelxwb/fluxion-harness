"""MCP logical audit facts (transport attempts are a separate observer)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class McpAuditEvent:
    server_key: str
    tool_name: str
    call_id: str
    rpc_method: str
    request_id: str
    http_status: int | None
    rpc_error_code: str | None
    is_error: bool
    sent: bool
    outcome_known: bool
    result_status: str
    error_code: str | None
    latency_ms: int
    attempt_id: str
    business_trace_id: UUID | None = None


class McpAuditPort(Protocol):
    async def record(self, event: McpAuditEvent) -> None: ...


__all__ = ["McpAuditEvent", "McpAuditPort"]
