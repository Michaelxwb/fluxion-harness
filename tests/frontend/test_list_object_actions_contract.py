"""Object actions belong to fixed list columns; association actions stay in details."""
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "apps/console-platform/frontend/src"


@pytest.mark.parametrize("module,sheet,page", [
    ("agent-management", "AgentDetailSideSheet", "AgentPage"),
    ("skill-management", "SkillDetailSideSheet", "SkillPage"),
    ("mcp-management", "McpDetailSideSheet", "McpPage"),
    ("model-management", "ModelDetailSideSheet", "ModelPage"),
    ("project-platform", "PlatformDetailSideSheet", "PlatformPage"),
    ("user-identity", "UserDetailTabs", "UserPage"),
    ("task-schedule", "TaskDetailSideSheet", "TaskPage"),
    ("task-schedule", "ScheduleDetailSideSheet", "SchedulePage"),
])
def test_object_actions_are_in_main_lists(module: str, sheet: str, page: str) -> None:
    detail = (SRC / "modules" / module / f"{sheet}.tsx").read_text()
    listing = (SRC / "modules" / module / f"{page}.tsx").read_text()
    assert "actions={" not in detail
    assert "...actionColumn(" in listing


def test_fixed_actions_use_explicit_keys_and_shared_table() -> None:
    layout = (SRC / "components/common/tableLayout.ts").read_text()
    assert "fixed: 'right'" in layout
    assert "key: 'actions'" in layout
    assert "column.key === 'actions'" in layout
    for name in ("DataTable", "RemoteTable"):
        source = (SRC / f"components/common/{name}.tsx").read_text()
        assert "tableLayout" in source or "DataTable<T>" in source
    audit = (SRC / "modules/audit-observability/components/AuditTable.tsx").read_text()
    assert "actionColumn" not in audit, "Audit event action is data, not a button column"


def test_copy_icons_preserve_value_accessibility_and_errors() -> None:
    button = (SRC / "components/common/CopyButton.tsx").read_text()
    text = (SRC / "components/common/CopyableText.tsx").read_text()
    for contract in ("IconCopy", "aria-label={title}", "Tooltip", "stopPropagation()",
                     "navigator.clipboard.writeText(value)", "Toast.error"):
        assert contract in button
    assert "CopyButton value={full}" in text


def test_relation_controls_and_run_refresh_remain_in_details() -> None:
    agent = (SRC / "modules/agent-management/AgentDetailSideSheet.tsx").read_text()
    for control in ("bind-skill-select", "bind-mcp-select", "grant-user-select"):
        assert control in agent
    platform = (SRC / "modules/project-platform/PlatformCredentialTab.tsx").read_text()
    assert "shared" in platform.lower()
    run = (SRC / "modules/run-observability/RunDetailSideSheet.tsx").read_text()
    assert "actions={" in run and "refresh" in run.lower()
