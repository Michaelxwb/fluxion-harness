"""[B-129 / RULE-07 / RULE-test-001] 验收收口：场景与规则唯一负责人、真实边界、证据登记与无遗留规划项。

结构检查：读取 `.acceptance-manifest.json`（由 Acceptance Coverage 表生成）与任务文档本身，
断言最终负责人唯一、无遗漏、无未终态行、E2E 命令走真实边界且无 mock、未终态行不冒充 verified。
本文件只做结构/证据核对，跨服务行为证据由各 owner TASK 的真实验收与仓库级 verifier 承载。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[3]
TASK_DIR = ROOT / ".code-flow/tasks/archived/2026-09-17/10-im-gateway"
TASK_FILE = TASK_DIR / "10-im-gateway.md"
MANIFEST = TASK_DIR / ".acceptance-manifest.json"

TERMINAL_STATUSES = {"verified", "e2e_deferred"}
# 本收口任务自身承接的行（B-129 / RULE-07 / RULE-test-001）在 finish 时终态化，
# 结构检查不提前替它们下结论；其余任何行的未终态都必须被本文件拦下。
CLOSING_TASK = "TASK-029"
COVERAGE_ROW = re.compile(r"^(?:[BSE]-\d|(?:RULE|RISK)-[A-Za-z0-9_-]+)")
MOCK_MARKERS = ("unittest.mock", "tests.gateway.fakes", "route.fulfill", "respx", "responses.")
# 设计 Spec Compliance Matrix 的 required Rule（本模块必须逐一登记最终负责人与命令）
REQUIRED_RULES = (
    "RULE-arch-001",
    "RULE-api-001",
    "RULE-api-002",
    "RULE-secret-001",
    "RULE-im-001",
    "RULE-auth-001",
    "RULE-snapshot-001",
    "RULE-data-001",
    "RULE-test-001",
)


def _manifest() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(MANIFEST.read_text(encoding="utf-8")))


def _text() -> str:
    return TASK_FILE.read_text(encoding="utf-8")


def _coverage_rows() -> list[dict[str, str]]:
    """全局 Acceptance Coverage 表的每一行（场景与规则）。"""
    rows: list[dict[str, str]] = []
    for line in _text().splitlines():
        if not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 10 and COVERAGE_ROW.match(cells[0]):
            rows.append(
                {
                    "id": cells[0],
                    "level": cells[2],
                    "owner": cells[4],
                    "status": cells[5],
                    "command": cells[6],
                }
            )
    return rows


def _task_section(task_id: str) -> str:
    match = re.search(rf"(?ms)^##\s+{re.escape(task_id)}:.*?(?=^##\s+TASK-|\Z)", _text())
    assert match, f"缺少任务段 {task_id}"
    return match.group(0)


def _evidence_section(task_id: str) -> str:
    section = _task_section(task_id)
    assert "### Acceptance Evidence" in section, f"{task_id} 缺 Acceptance Evidence 段"
    return section.split("### Acceptance Evidence", 1)[1]


def test_b129_scenarios_have_unique_owner_and_no_planned_left() -> None:
    scenarios = _manifest()["scenarios"]
    ids = [row["id"] for row in scenarios]
    assert len(set(ids)) == len(ids), "场景 ID 必须唯一"
    for row in scenarios:
        assert re.fullmatch(r"TASK-\d{3}", row.get("owner", "")), row
        if row["owner"] == CLOSING_TASK:
            continue
        assert row.get("status") in TERMINAL_STATUSES, row


def test_b129_coverage_rows_are_unique_owned_and_terminal() -> None:
    rows = _coverage_rows()
    assert len(rows) >= len(_manifest()["scenarios"]), "覆盖表行数不得少于场景数"
    ids = [row["id"] for row in rows]
    assert len(set(ids)) == len(ids), f"覆盖表出现重复行：{[i for i in ids if ids.count(i) > 1]}"
    for row in rows:
        assert re.fullmatch(r"TASK-\d{3}", row["owner"]), row
        _task_section(row["owner"])
        if row["owner"] == CLOSING_TASK:
            continue
        assert row["status"] in TERMINAL_STATUSES, row


def test_b129_every_owner_lists_row_in_acceptance_refs() -> None:
    for row in _coverage_rows():
        section = _task_section(row["owner"])
        refs = re.search(r"(?m)^- \*\*Acceptance-Refs\*\*:[ \t]*([^\n]*)", section)
        assert refs, f"{row['owner']} 缺 Acceptance-Refs"
        assert re.search(rf"\b{re.escape(row['id'])}\b", refs.group(1)), (row["owner"], row["id"])


def test_b129_verified_scenarios_are_registered_in_owner_evidence() -> None:
    for row in _manifest()["scenarios"]:
        if row["owner"] == CLOSING_TASK or row.get("status") not in TERMINAL_STATUSES:
            continue
        evidence = _evidence_section(row["owner"])
        assert re.search(rf"\b{re.escape(row['id'])}\b", evidence), (
            f"{row['id']} 标为 {row['status']} 但 {row['owner']} 的 Acceptance Evidence 未登记"
        )


def test_b129_e2e_commands_use_real_boundaries_without_mocks() -> None:
    for row in _coverage_rows():
        for marker in MOCK_MARKERS:
            assert marker not in row["command"], (row["id"], marker)
        if row["level"] == "E2E":
            assert "tests/acceptance" in row["command"] or "playwright" in row["command"], row
    for path in (ROOT / "tests/acceptance/im_gateway").glob("*.py"):
        if path.name == Path(__file__).name:  # 本文件只在 MOCK_MARKERS 里引用这些字面量
            continue
        source = path.read_text(encoding="utf-8")
        for marker in MOCK_MARKERS:
            assert marker not in source, (path.name, marker)


def test_b129_task_contract_rows_are_terminal() -> None:
    """每个负责任务的 Acceptance Contract 行同样必须闭合（全局覆盖表之外的这一层易漏）。"""
    for match in re.finditer(r"(?ms)^##\s+(TASK-\d+):.*?(?=^##\s+TASK-|\Z)", _text()):
        for line in match.group(0).splitlines():
            if not line.startswith("| "):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) != 7 or not COVERAGE_ROW.match(cells[0]):
                continue
            assert cells[-1] in TERMINAL_STATUSES, (match.group(1), cells[0], cells[-1])


def test_b129_done_tasks_have_no_unchecked_items() -> None:
    """done 任务的 checklist 必须全部勾选：Done Gate 不校验勾选状态，由此处兜住。"""
    for match in re.finditer(r"(?ms)^##\s+(TASK-\d+):.*?(?=^##\s+TASK-|\Z)", _text()):
        section = match.group(0)
        status = re.search(r"(?m)^- \*\*Status\*\*:([^\n]*)", section)
        assert status, match.group(1)
        if "done" not in status.group(1):
            continue
        unchecked = re.findall(r"(?m)^- \[ \][^\n]*", section)
        assert not unchecked, (match.group(1), unchecked[:2])


def test_b129_no_skip_or_xfail_claims_verified() -> None:
    for path in TASK_DIR.glob("*.md"):
        assert "pytest.skip" not in path.read_text(encoding="utf-8"), path
    for path in (ROOT / "tests/acceptance/im_gateway").glob("*.py"):
        if path.name == Path(__file__).name:  # 本文件只在断言里引用这两个字面量
            continue
        source = path.read_text(encoding="utf-8")
        assert "pytest.mark.xfail" not in source, path.name
        assert "pytest.skip" not in source, path.name


def test_b129_required_rules_are_registered_with_owner_and_command() -> None:
    rows = {row["id"]: row for row in _coverage_rows()}
    for rule in REQUIRED_RULES:
        assert rule in rows, f"缺 {rule} 的最终负责人登记"
        assert rows[rule]["command"].startswith("["), rows[rule]
    for row in rows.values():
        if row["id"].startswith("RULE-") or row["id"].startswith("RISK-"):
            assert row["command"].startswith("["), row
