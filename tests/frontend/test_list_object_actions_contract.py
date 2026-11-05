"""Object actions belong to fixed list columns; association actions stay in details."""
import re
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
                     "navigator.clipboard.writeText(value)", "Tips.error"):
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


@pytest.mark.parametrize("path,call,expected", [
    ("agent-management/AgentPage", "getAgent", 1),
    ("project-platform/PlatformPage", "getPlatform", 1),
    ("project-platform/PlatformPage", "getAdapters", 1),
    ("project-platform/ProjectPlatformForm", "getAdapters", 1),
    ("skill-management/SkillPage", "getSkill", 1),
    ("mcp-management/McpPage", "getMcpServer", 2),
    ("agent-management/AgentFormModal", "listModels", 1),
    ("model-management/ModelCredentialPanel", "listModels", 1),
    ("model-management/ModelCredentialPanel", "listQuotaGroups", 1),
    ("model-management/ModelCreateWizard", "listQuotaGroups", 1),
    ("learning/FamilyDetailSideSheet", "getFamily", 2),
    ("learning/RunLearningPanel", "getRunLearningDiagnostics", 1),
    ("project-platform/PlatformCredentialTab", "listUsers", 1),
    ("user-identity/UserDetailTabs", "listAgentsForPicker", 1),
    ("skill-management/SelectedUserTable", "listUsers", 1),
    ("mcp-management/SelectedUserTable", "listUsers", 1),
    ("task-schedule/TaskPage", "listAgentsForPicker", 1),
    ("task-schedule/TaskPage", "listUsers", 1),
    ("task-schedule/TaskPage", "listSkills", 1),
])
def test_get_actions_without_inline_errors_request_visible_error_tips(
    path: str, call: str, expected: int,
) -> None:
    source = (SRC / "modules" / f"{path}.tsx").read_text()
    calls = re.findall(rf"\b{call}\(([^;]*?)\)(?:\s*\.then|\s*\))?", source)
    assert len(calls) == expected
    assert all("errorTip: 'always'" in arguments for arguments in calls), (
        f"{path} 的 {call} 失败没有内联错误，必须显式保留全局提示"
    )
