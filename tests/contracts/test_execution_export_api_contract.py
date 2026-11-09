"""API contract for single-execution export routes and headers."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "apps/console-platform/backend/src/muad_console_platform/api"
SERVICE = (
    ROOT / "apps/console-platform/backend/src/muad_console_platform/application/execution_export_service.py"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_run_export_route_is_admin_and_tenant_scoped() -> None:
    source = _read(API / "runs.py")
    assert '@router.get("/{run_id}/export")' in source
    assert "AdminAccount" in source
    assert "ExecutionExportService" in source
    assert "execution_export_response" in source


def test_task_export_route_is_admin_and_tenant_scoped() -> None:
    source = _read(API / "tasks.py")
    assert '@router.get("/{task_id}/export")' in source
    assert "AdminAccount" in source
    assert "ExecutionExportService" in source
    assert "execution_export_response" in source


def test_export_response_headers_and_formats() -> None:
    response = _read(API / "execution_export.py")
    assert "Content-Disposition" in response
    assert "Cache-Control" in response
    assert "no-store" in response
    assert "X-Export-Truncated" in response
    assert "X-Export-Complete" in response
    assert "X-Export-Event-Count" in response

    service = _read(SERVICE)
    assert 'SUPPORTED_FORMATS = frozenset({"ZIP", "JSON"})' in service
    assert "COMMON_VALIDATION_ERROR" in service
    assert "COMMON_NOT_FOUND" in service


def test_deployment_unit_is_console_only_read_model() -> None:
    """The exporter is a Console read-only projection; no new service/deploy unit."""
    source = _read(SERVICE)
    assert "ExecutionExportRepository" in source
    assert "ExecutionExportService" in source
    assert "create_task" not in source
