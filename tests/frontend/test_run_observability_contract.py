"""Run 只读视图的源码契约（形态 A：嵌套 SideSheet，不新增导航页）。

三条约束在这里机检，避免只靠约定：

1. **不新增页面/导航项**——Run 用嵌套 SideSheet 承载，`menu.ts` 不出现 Run 条目；
2. **不暴露内容**——前端不引用 `input_text` / `payload`（后端本来也不返回，两侧同向）；
3. **不再"统一落到任务列表"**——Run 关联就地打开，Task 关联才跳列表。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "apps/console-platform/frontend/src"

RUN_SERVICE = SRC / "modules/run-observability/services/runs.ts"
RUN_SHEET = SRC / "modules/run-observability/RunDetailSideSheet.tsx"
AUDIT_DETAIL = SRC / "modules/audit-observability/components/AuditDetailSideSheet.tsx"
TASK_DETAIL = SRC / "modules/task-schedule/TaskDetailSideSheet.tsx"
TASK_PAGE = SRC / "modules/task-schedule/TaskPage.tsx"
MENU = SRC / "config/menu.ts"
LOCALES = ("zh-CN", "en-US")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_run_view_does_not_add_a_navigation_entry() -> None:
    """形态 A 的前提：Run 不开独立页面。开了页面就得同时改导航与 sidebar e2e 的计数。"""
    menu = _read(MENU)
    assert "/runs" not in menu
    assert "nav.run" not in menu


def test_run_service_uses_the_shared_api_client() -> None:
    source = _read(RUN_SERVICE)
    assert "from '../../../api/client'" in source
    assert "api.get" in source
    assert "axios" not in source
    assert "fetch(" not in source
    assert "export async function getRun" in source


def test_run_view_never_reads_conversation_content() -> None:
    """Console 至今零暴露对话原文；这条把「不回退」钉住，而不是靠口头约定。

    检查的是前端**有没有给这两个字段起名**（DTO 键或渲染引用）。源码注释里刻意不写它们的
    字面量，否则解释「为什么不给」的那句话自己就会把用例打红。
    """
    for path in (RUN_SERVICE, RUN_SHEET):
        source = _read(path)
        for token in ("input_text", "inputText", "payload", "payload_json"):
            assert token not in source, f"{path}: {token}"


def test_audit_run_relation_opens_in_place_rather_than_jumping_to_tasks() -> None:
    audit = _read(AUDIT_DETAIL)
    assert "setOpenRunId(id)" in audit
    assert "navigate(`/tasks?taskId=${id}`)" in audit
    # Run 不再走"统一落到任务列表"那条路
    assert "navigate(`/tasks?${relation}Id=" not in audit
    assert "<RunDetailSideSheet" in audit


def test_task_detail_links_back_to_its_source_run() -> None:
    detail = _read(TASK_DETAIL)
    assert "detail.source_run_id" in detail
    assert 'testId="task-detail-run"' in detail
    assert "onOpenRun" in detail
    assert "useNavigate" not in detail, "详情组件只回调，不自行导航"
    assert "<RunDetailSideSheet" in _read(TASK_PAGE)


def test_run_detail_reports_truncation_instead_of_pretending_completeness() -> None:
    sheet = _read(RUN_SHEET)
    assert "timeline_truncated" in sheet
    assert 'data-testid="run-detail-truncated"' in sheet


def test_run_labels_are_localized_in_both_locales() -> None:
    import json

    for locale in LOCALES:
        payload = json.loads(_read(SRC / f"locales/{locale}.json"))
        for status in ("CREATED", "RUNNING", "WAITING_INPUT", "COMPLETED", "FAILED", "CANCELLED"):
            assert payload[f"run.status.{status}"], (locale, status)
