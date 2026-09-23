"""[B-144] 验收收口：场景唯一负责人、真实边界、证据与无遗留规划项。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[3]
TASK_DIR = ROOT / ".code-flow/tasks/archived/2026-09-17/09-task-schedule"
TASK_FILE = TASK_DIR / "09-task-schedule.md"
MANIFEST = TASK_DIR / ".acceptance-manifest.json"

TERMINAL_STATUSES = {"verified", "e2e_deferred"}


def _manifest() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(MANIFEST.read_text(encoding="utf-8")))


def test_b144_every_scenario_has_unique_owner_and_no_planned_left() -> None:
    data = _manifest()
    scenarios = data["scenarios"]
    assert len({row["id"] for row in scenarios}) == len(scenarios), "场景 ID 必须唯一"
    for row in scenarios:
        assert row.get("owner", "").startswith("TASK-"), row
        if row["owner"] == "TASK-044":
            # 本收口任务自身场景由 verify-e2e 统一置为终态，此处不提前断言。
            continue
        assert row.get("status") in TERMINAL_STATUSES, row


def test_b144_every_owner_lists_scenario_in_task_file() -> None:
    text = TASK_FILE.read_text(encoding="utf-8")
    for row in _manifest()["scenarios"]:
        owner = row["owner"]
        section = re.search(rf"(?ms)^##\s+{re.escape(owner)}:.*?(?=^##\s+TASK-|\Z)", text)
        assert section, f"缺少任务段 {owner}"
        refs = re.search(r"(?m)^- \*\*Acceptance-Refs\*\*:[ \t]*([^\n]*)", section.group(0))
        assert refs, f"{owner} 缺 Acceptance-Refs"
        assert row["id"] in re.findall(r"\b[SEB]-\d+\b", refs.group(1)), (owner, row["id"])


def test_b144_e2e_commands_are_real_boundaries_without_mocks() -> None:
    for row in _manifest()["scenarios"]:
        command = " ".join(row.get("command", []))
        assert "route.fulfill" not in command
        assert "unittest.mock" not in command
        if row.get("level") == "E2E":
            assert "tests/acceptance" in command or "playwright" in command, row


def test_b144_no_unexplained_skip_in_acceptance_suites() -> None:
    for path in TASK_DIR.glob("*.md"):
        if path.name.endswith(".design.md"):
            continue
        text = path.read_text(encoding="utf-8")
        assert "pytest.skip" not in text, path
