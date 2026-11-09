"""TASK-005: Run/Task detail "导出链路" frontend contract."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "apps/console-platform/frontend/src"
MODULE = SRC / "modules/execution-export"
BUTTON = MODULE / "components/ExportExecutionButton.tsx"
HOOK = MODULE / "hooks/useExecutionExport.ts"
SERVICE = MODULE / "services/executionExport.ts"
RUN_SHEET = SRC / "modules/run-observability/RunDetailSideSheet.tsx"
TASK_SHEET = SRC / "modules/task-schedule/TaskDetailSideSheet.tsx"
LOCALES = SRC / "locales"


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _locale(name: str) -> dict[str, str]:
    return json.loads(_read(LOCALES / f"{name}.json"))


def test_export_button_contract() -> None:
    source = _read(BUTTON)
    assert "useAuth" in source
    assert "account?.role !== 'ADMIN'" in source
    assert 'data-testid="execution-export"' in source
    assert "executionExport.action" in source
    assert "executionExport.tooltip" in source
    assert "IconDownload" in source
    assert "loading={busy}" in source
    assert "disabled={props.disabled || busy}" in source


def test_service_is_the_only_http_boundary() -> None:
    source = _read(SERVICE)
    assert "api.get<Blob>" in source
    assert "responseType: 'blob'" in source
    assert "/export" in source
    assert "parseDownloadFilename" in source
    assert "axios" not in source and "fetch(" not in source


def test_hook_owns_busy_error_and_download() -> None:
    source = _read(HOOK)
    assert "downloadExecutionExport" in source
    assert "saveBlob" in source
    assert "Tips.error" in source
    assert "busyRef.current" in source
    assert "executionExport.notFound" in source
    assert "axios" not in source and "fetch(" not in source and "api.get(" not in source


def test_run_and_task_details_mount_the_export_button() -> None:
    run = _read(RUN_SHEET)
    assert "<ExportExecutionButton" in run
    assert 'kind="RUN"' in run
    assert "id={props.runId}" in run

    task = _read(TASK_SHEET)
    assert "<ExportExecutionButton" in task
    assert 'kind="TASK"' in task
    assert "id={props.taskId}" in task
    # Task 详情属于列表对象：全局 UI 契约禁止在侧滑 actions 放对象操作；
    # 导出是诊断动作，放在基本信息区标题行内。
    assert "actions={" not in task
    assert "detail-section-title" in task


def test_both_locales_have_export_labels() -> None:
    for locale in ("zh-CN", "en-US"):
        payload = _locale(locale)
        for key in (
            "executionExport.action",
            "executionExport.tooltip",
            "executionExport.busy",
            "executionExport.failed",
            "executionExport.notFound",
            "executionExport.ariaLabel",
        ):
            assert payload.get(key), (locale, key)
