"""Orchestrate one execution export: collect -> diagnose -> render -> response metadata."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.execution_export_repository import (
    ExecutionExportBundle,
    ExecutionExportRepository,
)
from ..metrics import EXECUTION_EXPORT_METRIC, FAILED_STATUS, SUCCESS_STATUS, record_outcome
from .execution_diagnosis import build_diagnosis
from .execution_export_render import RenderedExport, render_report
from .execution_export_settings import ExecutionExportSettings
from .execution_export_view import build_report

logger = logging.getLogger(__name__)
SUPPORTED_FORMATS = frozenset({"ZIP", "JSON"})
BundleFactory = Callable[[], Awaitable[ExecutionExportBundle | None]]


@dataclass(frozen=True)
class ExportedExecution:
    content: bytes
    media_type: str
    filename: str
    truncated: bool
    complete: bool
    event_count: int


def _format(value: str) -> str:
    normalized = value.upper()
    if normalized not in SUPPORTED_FORMATS:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    return normalized


def _suffix(store_format: str) -> str:
    return "zip" if store_format == "ZIP" else "json"


class ExecutionExportService:
    def __init__(self, session: AsyncSession, settings: ExecutionExportSettings | None = None) -> None:
        self._settings = settings or ExecutionExportSettings.from_shared()
        self._repository = ExecutionExportRepository(session, self._settings)

    async def export_run(
        self,
        tenant_id: str,
        run_id: uuid.UUID,
        store_format: str,
        *,
        locale: str = "zh-CN",
    ) -> ExportedExecution:
        return await self._export(
            kind="RUN",
            execution_id=run_id,
            store_format=store_format,
            locale=locale,
            bundle_factory=lambda: self._repository.run_bundle(tenant_id, run_id),
        )

    async def export_task(
        self,
        tenant_id: str,
        task_id: uuid.UUID,
        store_format: str,
        *,
        locale: str = "zh-CN",
    ) -> ExportedExecution:
        return await self._export(
            kind="TASK",
            execution_id=task_id,
            store_format=store_format,
            locale=locale,
            bundle_factory=lambda: self._repository.task_bundle(tenant_id, task_id),
        )

    async def _export(
        self,
        *,
        kind: str,
        execution_id: uuid.UUID,
        store_format: str,
        locale: str,
        bundle_factory: BundleFactory,
    ) -> ExportedExecution:
        normalized = _format(store_format)
        try:
            bundle: ExecutionExportBundle | None = await bundle_factory()
            if bundle is None:
                raise AppError(ErrorCode.COMMON_NOT_FOUND)
            diagnosis = build_diagnosis(bundle)
            report = build_report(bundle, diagnosis, locale=locale)
            rendered: RenderedExport = render_report(report, normalized, max_bytes=self._settings.max_bytes)
            filename = f"{kind.lower()}-{execution_id}-{diagnosis.outcome.lower()}.{rendered.extension}"
            logger.info(
                "execution_export",
                extra={
                    "kind": kind,
                    "execution_id": str(execution_id),
                    "format": normalized,
                    "event_count": len(report.get("timeline", [])),
                    "truncated": bool(report.get("truncation", {}).get("truncated")),
                    "complete": bool(report.get("data_quality", {}).get("complete")),
                    "size_bytes": len(rendered.content),
                },
            )
            record_outcome(
                EXECUTION_EXPORT_METRIC,
                SUCCESS_STATUS,
                {"kind": kind, "format": normalized},
            )
        except AppError as error:
            record_outcome(
                EXECUTION_EXPORT_METRIC,
                error.code,
                {"kind": kind, "format": normalized},
            )
            raise
        except Exception:
            record_outcome(
                EXECUTION_EXPORT_METRIC,
                FAILED_STATUS,
                {"kind": kind, "format": normalized},
            )
            raise
        return ExportedExecution(
            content=rendered.content,
            media_type=rendered.media_type,
            filename=filename,
            truncated=bool(report.get("truncation", {}).get("truncated")),
            complete=bool(report.get("data_quality", {}).get("complete")),
            event_count=len(report.get("timeline", [])),
        )
