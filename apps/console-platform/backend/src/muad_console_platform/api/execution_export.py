"""Shared binary response for Run/Task execution-chain exports."""

from __future__ import annotations

from fastapi import Response

from ..application.execution_export_service import ExportedExecution


def execution_export_response(result: ExportedExecution) -> Response:
    return Response(
        content=result.content,
        media_type=result.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{result.filename}"',
            "Cache-Control": "no-store",
            "X-Export-Truncated": "true" if result.truncated else "false",
            "X-Export-Complete": "true" if result.complete else "false",
            "X-Export-Event-Count": str(result.event_count),
        },
    )
