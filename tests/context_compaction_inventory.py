"""Inventory for conversation-context-retention: task document ↔ tests ↔ specs ↔ scenarios."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENT = ROOT / ".code-flow/tasks/2026-11-23/conversation-context-retention"
TASK_DOC = REQUIREMENT / "conversation-context-retention.md"
SCENARIOS = REQUIREMENT / "conversation-context-retention.scenarios.md"
SPECS = ROOT / ".code-flow/specs"
CUTOVER = ROOT / "docs/operations/conversation-context-retention-cutover.md"


def _text(path: Path) -> str:
    assert path.is_file(), f"missing {path}"
    return path.read_text(encoding="utf-8")


def _tasks(text: str) -> dict[str, str]:
    parts = re.split(r"(?m)^## (TASK-\d+: .*)$", text)
    return {parts[index].split(":", 1)[0]: parts[index + 1] for index in range(1, len(parts), 2)}


def test_requirement_documents_and_cutover_are_present() -> None:
    for name in (
        "conversation-context-retention.prd.md",
        "conversation-context-retention.backend.design.md",
        "conversation-context-retention.frontend.design.md",
        "conversation-context-retention.scenarios.md",
        "conversation-context-retention.md",
    ):
        assert (REQUIREMENT / name).is_file(), name
    assert CUTOVER.is_file()


def test_every_declared_test_path_exists() -> None:
    text = _text(TASK_DOC)
    tasks = _tasks(text)
    assert len(tasks) == 8
    for task_id, body in tasks.items():
        block = re.search(r"### Tests\s+```json\s+(.*?)```", body, re.S)
        assert block is not None, task_id
        commands = json.loads(block.group(1))
        assert commands, task_id
        for command in commands:
            for part in command["argv"]:
                if part.startswith("tests/"):
                    assert (ROOT / part).is_file(), f"{task_id} missing test {part}"


def test_spec_refs_and_scenarios_resolve() -> None:
    scenario_text = _text(SCENARIOS)
    scenario_ids = set(re.findall(r"^## ([SEB]-?\d+)", scenario_text, re.M))
    for task_id, body in _tasks(_text(TASK_DOC)).items():
        refs = re.search(r"^- \*\*Spec-Refs\*\*: (.+)$", body, re.M)
        assert refs is not None, task_id
        for ref in (item.strip() for item in refs.group(1).split(",")):
            assert (SPECS / f"{ref}.md").is_file(), f"{task_id} missing spec {ref}"
        scenarios = re.search(r"^- \*\*Scenarios\*\*: (.+)$", body, re.M)
        assert scenarios is not None, task_id
        for scenario in (item.strip() for item in scenarios.group(1).split(",")):
            assert scenario in scenario_ids, f"{task_id} missing scenario {scenario}"
