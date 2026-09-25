"""[B-211 / RULE-06 / RULE-test-001] 验收收口：场景与规则唯一负责人、真实边界、证据登记与无遗留规划项。

结构检查：读取 `.acceptance-manifest.json`（由 Acceptance Coverage 表生成）与任务文档本身，
断言最终负责人唯一、无遗漏、无未终态行、E2E 命令走真实边界且无 mock、未终态行不冒充 verified。
本文件只做结构/证据核对，跨服务行为证据由各 owner TASK 的真实验收与仓库级 verifier 承载。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT / ".code-flow/tasks/2026-09-17/11-audit-observability"
TASK_FILE = TASK_DIR / "11-audit-observability.md"
MANIFEST = TASK_DIR / ".acceptance-manifest.json"

MODULE_TEST_DIR = ROOT / "tests/acceptance/audit_observability"
PLAYWRIGHT_SPEC = ROOT / "e2e/tests/audit-observability.spec.ts"

TERMINAL_STATUSES = {"verified", "e2e_deferred"}
# 本收口任务自身承接的行（B-211 / RULE-06 / RULE-test-001）在 finish 时终态化，
# 结构检查不提前替它们下结论；其余任何行的未终态都必须被本文件拦下。
CLOSING_TASK = "TASK-019"
# 本模块的 E2E 场景（后端 S-01/03/05 与前端 S-06..S-08；E-06..E-09 为浏览器失败/边界路径）。
MODULE_E2E_SCENARIOS = (
    "S-01",
    "S-03",
    "S-05",
    "S-06",
    "S-07",
    "S-08",
    "E-06",
    "E-07",
    "E-08",
    "E-09",
)
# 设计/TASK-018 明确允许失败与边界路径（integration 层级）用路由拦截制造失败，
# 故 mock 标记只允许出现在这几个场景块内；E2E 层级场景一律不允许。
ROUTE_INTERCEPTION_SCENARIOS = frozenset({"E-06", "E-07", "E-08", "E-09"})
COVERAGE_ROW = re.compile(r"^(?:[BSE]-\d|(?:RULE|RISK)-[A-Za-z0-9_-]+)")
SCENARIO_ID = re.compile(r"^[BSE]-\d+$")
PY_PATH_IN_COMMAND = re.compile(r"tests/[\w/.\-]+\.py")
MOCK_MARKERS = ("unittest.mock", "tests.gateway.fakes", "route.fulfill", "respx", "responses.")
SKIP_MARKERS = ("pytest.mark.xfail", "pytest.skip")
# Coverage 表的 11 条 required Spec Rule（harness-api×2 + 其余九域各一），id 已按 Context 归一。
REQUIRED_RULES = (
    "RULE-api-001",
    "RULE-api-002",
    "RULE-test-001",
    "RULE-data-001",
    "RULE-ui-001",
    "RULE-front-001",
    "RULE-i18n-001",
    "RULE-log-001",
    "RULE-secret-001",
    "RULE-time-001",
    "RULE-ui-detail-001",
)


def _text() -> str:
    return TASK_FILE.read_text(encoding="utf-8")


def _manifest() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(MANIFEST.read_text(encoding="utf-8")))


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


def _task_sections() -> list[tuple[str, str]]:
    return [
        (match.group(1), match.group(0))
        for match in re.finditer(r"(?ms)^##\s+(TASK-\d+):.*?(?=^##\s+TASK-|\Z)", _text())
    ]


def _task_section(task_id: str) -> str:
    for found_id, section in _task_sections():
        if found_id == task_id:
            return section
    raise AssertionError(f"缺少任务段 {task_id}")


def _acceptance_refs(task_id: str) -> str:
    refs = re.search(r"(?m)^- \*\*Acceptance-Refs\*\*:[ \t]*([^\n]*)", _task_section(task_id))
    assert refs, f"{task_id} 缺 Acceptance-Refs"
    return refs.group(1)


def _evidence_section(task_id: str) -> str:
    section = _task_section(task_id)
    assert "### Acceptance Evidence" in section, f"{task_id} 缺 Acceptance Evidence 段"
    return section.split("### Acceptance Evidence", 1)[1]


def _contract_rows(section: str) -> list[tuple[str, str]]:
    """任务段内 Acceptance Contract 表(7 列)的每一行 → (场景ID, 状态)。"""
    rows: list[tuple[str, str]] = []
    for line in section.splitlines():
        if not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 7 and COVERAGE_ROW.match(cells[0]):
            rows.append((cells[0], cells[-1]))
    return rows


def _playwright_blocks() -> dict[str, str]:
    """playwright spec 按 `test('<场景ID> ...` 切成场景块，供 mock 标记定位。"""
    blocks: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in PLAYWRIGHT_SPEC.read_text(encoding="utf-8").splitlines():
        start = re.match(r"^test\('([BSE]-\d+)", line)
        if start:
            if current is not None:
                blocks[current] = "\n".join(buffer)
            current, buffer = start.group(1), [line]
        elif current is not None:
            buffer.append(line)
    if current is not None:
        blocks[current] = "\n".join(buffer)
    return blocks


def _assert_command_targets_real_suite(command: str) -> None:
    """E2E 行的命令必须指向仓库真实存在的用例文件或真实前端/浏览器入口。"""
    targets = PY_PATH_IN_COMMAND.findall(command)
    assert targets or "playwright" in command or "npm --prefix" in command, command
    for rel_path in targets:
        assert (ROOT / rel_path).exists(), f"E2E 命令引用的用例文件不存在：{rel_path}"


def test_b211_coverage_rows_are_unique_owned_and_terminal() -> None:
    rows = _coverage_rows()
    assert rows, "Acceptance Coverage 表为空"
    ids = [row["id"] for row in rows]
    assert len(set(ids)) == len(ids), f"覆盖表出现重复行：{[i for i in ids if ids.count(i) > 1]}"
    for row in rows:
        assert re.fullmatch(r"TASK-\d{3}", row["owner"]), row
        _task_section(row["owner"])
        if row["owner"] == CLOSING_TASK:
            continue
        assert row["status"] in TERMINAL_STATUSES, row


def test_b211_manifest_agrees_with_coverage_table() -> None:
    rows = {row["id"]: row for row in _coverage_rows()}
    scenarios = _manifest()["scenarios"]
    ids = [row["id"] for row in scenarios]
    assert len(set(ids)) == len(ids), f"manifest 场景 ID 重复：{[i for i in ids if ids.count(i) > 1]}"
    for row in scenarios:
        assert row["id"] in rows, f"manifest 场景 {row['id']} 不在覆盖表中"
        for field in ("level", "owner", "status"):
            assert rows[row["id"]][field] == row[field], (row["id"], field, rows[row["id"]][field])
    coverage_scenario_ids = {row_id for row_id in rows if SCENARIO_ID.match(row_id)}
    assert coverage_scenario_ids == set(ids), coverage_scenario_ids ^ set(ids)


def test_b211_every_owner_lists_row_in_acceptance_refs() -> None:
    for row in _coverage_rows():
        refs = _acceptance_refs(row["owner"])
        assert re.search(rf"\b{re.escape(row['id'])}\b", refs), (row["owner"], row["id"])


def test_b211_terminal_scenarios_are_registered_in_owner_evidence() -> None:
    for row in _manifest()["scenarios"]:
        if row["owner"] == CLOSING_TASK or row["status"] not in TERMINAL_STATUSES:
            continue
        evidence = _evidence_section(row["owner"])
        assert re.search(rf"\b{re.escape(row['id'])}\b", evidence), (
            f"{row['id']} 标为 {row['status']} 但 {row['owner']} 的 Acceptance Evidence 未登记"
        )


def test_b211_e2e_commands_and_suites_use_real_boundaries_without_mocks() -> None:
    for row in _coverage_rows():
        for marker in MOCK_MARKERS:
            assert marker not in row["command"], (row["id"], marker)
        if row["level"] == "E2E":
            _assert_command_targets_real_suite(row["command"])
    for path in sorted(MODULE_TEST_DIR.glob("*.py")):
        if path.name == Path(__file__).name:  # 本文件只在 MOCK_MARKERS 里引用这些字面量
            continue
        source = path.read_text(encoding="utf-8")
        for marker in MOCK_MARKERS:
            assert marker not in source, (path.name, marker)
    blocks = _playwright_blocks()
    for scenario_id, block in blocks.items():
        for marker in MOCK_MARKERS:
            if marker in block:
                assert scenario_id in ROUTE_INTERCEPTION_SCENARIOS, (scenario_id, marker)
    for scenario_id in ("S-06", "S-07", "S-08"):
        assert scenario_id in blocks, f"playwright spec 缺 E2E 场景 {scenario_id}"


def test_b211_required_rules_are_registered_with_owner_and_command() -> None:
    assert len(REQUIRED_RULES) == 11, REQUIRED_RULES
    rows = {row["id"]: row for row in _coverage_rows()}
    for rule in REQUIRED_RULES:
        assert rule in rows, f"缺 {rule} 的最终负责人登记"
        assert re.fullmatch(r"TASK-\d{3}", rows[rule]["owner"]), rows[rule]
        _task_section(rows[rule]["owner"])
        assert rows[rule]["command"].startswith("["), rows[rule]
    for row in rows.values():
        if row["id"].startswith(("RULE-", "RISK-")):
            assert row["command"].startswith("["), row


def test_b211_task_contract_rows_and_done_checklists_are_closed() -> None:
    """每个 Responsibility 任务的 Acceptance Contract 行同样必须闭合，done 任务的 checklist 必须全勾。

    收口任务自身的契约行与 checklist 在 finish 时才终态化，此处不提前替它下结论；
    未 done 的任务不做勾选断言（Done Gate 不校验勾选状态，由此处兜住 done 任务）。
    """
    for task_id, section in _task_sections():
        if task_id != CLOSING_TASK:
            for row_id, status in _contract_rows(section):
                assert status in TERMINAL_STATUSES, (task_id, row_id, status)
        declared = re.search(r"(?m)^- \*\*Status\*\*:([^\n]*)", section)
        assert declared, task_id
        if not re.search(r"\b(done|verified)\b", declared.group(1)):
            continue
        unchecked = re.findall(r"(?m)^- \[ \][^\n]*", section)
        assert not unchecked, (task_id, unchecked[:2])


def test_b211_module_e2e_scenarios_are_terminal() -> None:
    rows = {row["id"]: row for row in _coverage_rows()}
    for scenario_id in MODULE_E2E_SCENARIOS:
        assert scenario_id in rows, f"覆盖表缺 {scenario_id}"
        row = rows[scenario_id]
        assert re.fullmatch(r"TASK-\d{3}", row["owner"]), row
        _task_section(row["owner"])
        if row["owner"] == CLOSING_TASK:
            continue
        assert row["status"] in TERMINAL_STATUSES, row


def test_b211_no_skip_or_xfail_claims_verified() -> None:
    for path in sorted(TASK_DIR.glob("*.md")):
        assert "pytest.skip" not in path.read_text(encoding="utf-8"), path
    for path in sorted(MODULE_TEST_DIR.glob("*.py")):
        if path.name == Path(__file__).name:  # 本文件只在断言里引用这两个字面量
            continue
        source = path.read_text(encoding="utf-8")
        for marker in SKIP_MARKERS:
            assert marker not in source, (path.name, marker)


def test_r06_e2e_scenarios_are_owned_and_run_real_boundary_suites() -> None:
    """RULE-06：跨 API/DB/Runtime/Browser 的 E2E 边界必须有唯一负责人且命令走真实套件。"""
    rows = {row["id"]: row for row in _coverage_rows()}
    rule = rows.get("RULE-06")
    assert rule, "覆盖表缺 RULE-06"
    assert rule["owner"] == CLOSING_TASK, rule
    assert "tests/audit_observability_inventory.py" in rule["command"], rule
    assert "-k r06" in rule["command"], rule
    assert re.search(r"\bRULE-06\b", _acceptance_refs(CLOSING_TASK)), "收口任务未登记 RULE-06"
    e2e_rows = [row for row in rows.values() if row["level"] == "E2E"]
    assert e2e_rows, "覆盖表无 E2E 行"
    for row in e2e_rows:
        assert re.fullmatch(r"TASK-\d{3}", row["owner"]), row
        assert re.search(rf"\b{re.escape(row['id'])}\b", _acceptance_refs(row["owner"])), row
        for marker in MOCK_MARKERS:
            assert marker not in row["command"], (row["id"], marker)
        _assert_command_targets_real_suite(row["command"])
