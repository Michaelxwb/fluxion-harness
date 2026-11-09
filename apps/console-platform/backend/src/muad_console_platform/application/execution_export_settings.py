"""Bounded budgets for one execution-chain export.

Diagnostic fields stay raw; these limits only keep a single download from becoming an
unbounded memory/file operation. Values live in `SharedSettings` so deployments can tune
them without touching the exporter.
"""

from __future__ import annotations

from dataclasses import dataclass

from muad_common import SharedSettings


@dataclass(frozen=True)
class ExecutionExportSettings:
    max_events: int = 10_000
    max_task_events: int = 10_000
    max_task_children: int = 500
    max_bytes: int = 20 * 1024 * 1024
    event_excerpt_before: int = 5
    event_excerpt_after: int = 5

    @classmethod
    def from_shared(cls, settings: SharedSettings | None = None) -> ExecutionExportSettings:
        current = settings or SharedSettings()
        return cls(
            max_events=current.execution_export_max_events,
            max_task_events=current.execution_export_max_task_events,
            max_task_children=current.execution_export_max_task_children,
            max_bytes=current.execution_export_max_bytes,
            event_excerpt_before=current.execution_export_event_excerpt_before,
            event_excerpt_after=current.execution_export_event_excerpt_after,
        )
