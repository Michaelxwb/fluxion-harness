"""MCP protocol/retry limits shared by Runtime and Worker."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class McpLimits:
    max_response_bytes: int = 4 * 1024 * 1024
    max_sse_event_bytes: int = 1024 * 1024
    max_content_items: int = 100
    max_artifact_bytes: int = 16 * 1024 * 1024
    max_total_bytes: int = 32 * 1024 * 1024


__all__ = ["McpLimits"]
