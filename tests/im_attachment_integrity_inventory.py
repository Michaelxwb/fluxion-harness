"""Cross-check the real requirement assets; no separate acceptance manifest."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.unit


def _requirement() -> Path:
    parent = ROOT / ".code-flow/tasks/2026-11-20"
    for path in (parent / "im-attachment-integrity",
                 ROOT / ".code-flow/tasks/archived/2026-11-20/im-attachment-integrity"):
        if path.is_dir():
            return path
    raise AssertionError("missing im-attachment-integrity requirement")


def _field(section: str, name: str) -> str:
    match = re.search(rf"^- \*\*{name}\*\*: (.*)$", section, re.M)
    assert match is not None, f"missing {name}"
    return match.group(1)


def _verify(document: str, *, finishing: bool = False) -> None:
    sections = re.findall(r"^## (TASK-\d+):[^\n]*\n(.*?)(?=^## TASK-|\Z)", document, re.M | re.S)
    assert [task for task, _ in sections] == [f"TASK-{i:03}" for i in range(1, 12)], "TASK coverage"
    scenarios = (_requirement() / "im-attachment-integrity.scenarios.md").read_text()
    documented = set(re.findall(r"^## ([SEB]-\d+)\b", scenarios, re.M))
    referenced: set[str] = set()
    for task, section in sections:
        expected = {"done", "in-progress"} if finishing and task == "TASK-011" else {"done"}
        assert _field(section, "Status") in expected, f"{task}: nonterminal status"
        tests = re.search(r"### Tests\s+```json\s+(.*?)\s+```", section, re.S)
        assert tests is not None, f"{task}: missing tests"
        for command in json.loads(tests.group(1)):
            _verify_command(command["argv"], task)
        for ref in _field(section, "Spec-Refs").split(", "):
            spec, rule = ref.split("#")
            content = (ROOT / ".code-flow/specs" / f"{spec}.md").read_text()
            assert f"[{rule}]" in content, f"{task}: missing spec {ref}"
        for ref in _field(section, "Scenarios").split(", "):
            scenario = ref.split(":")[-1]
            assert scenario in documented, f"{task}: missing scenario {scenario}"
            referenced.add(scenario)
    assert referenced == documented, f"orphan scenarios: {documented - referenced}"


def _verify_command(argv: list[str], task: str) -> None:
    assert argv, f"{task}: empty test command"
    if "pytest" not in argv:
        return
    paths = [part for part in argv if part.startswith("tests/")]
    assert paths, f"{task}: no test paths"
    for value in paths:
        parts = value.split("::")
        path = ROOT / parts[0]
        assert path.exists(), f"{task}: missing test path {value}"
        if len(parts) > 1:
            names = {n.name for n in ast.walk(ast.parse(path.read_text()))
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
            assert all(part in names for part in parts[1:]), f"{task}: missing test case {value}"


def test_task_specs_tests_and_scenarios_exist() -> None:
    # finish sets its own TASK done after tests. A strict terminal check is also
    # performed after finish; every other TASK must already be terminal here.
    _verify((_requirement() / "im-attachment-integrity.md").read_text(), finishing=True)


@pytest.mark.parametrize("old,new", [
    ("**Status**: done", "**Status**: draft"),
    ("tests/im_core/test_inbound_batch.py", "tests/im_core/not_a_test.py"),
    ("im/harness-im#RULE-im-002", "im/harness-im#RULE-missing"),
    ("im-attachment-integrity:S-01", "im-attachment-integrity:S-999"),
])
def test_inventory_rejects_broken_assets(old: str, new: str) -> None:
    document = (_requirement() / "im-attachment-integrity.md").read_text()
    assert old in document
    with pytest.raises(AssertionError):
        _verify(document.replace(old, new, 1))
