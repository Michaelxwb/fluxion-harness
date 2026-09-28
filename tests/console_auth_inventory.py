"""[收口] 13-console-auth 需求级闭合清单（TASK-012 / RULE-test-001 的承接面）。

以**真实盘面**为输入做闭合核对，而不是自查断言：任务文件的覆盖表/契约表/证据表/checkbox、
验收 manifest、spec-context 里的 required 规则，以及 E2E 套件是否真的在盘。

闭合口径（与 11-audit-observability / 12-overview-dashboard 的收口清单同口径）：
- 覆盖表每个场景只有一个负责人，且**每一行**（含 RULE 规则行）全部终态；
- manifest 与覆盖表同 ID 同 owner 同命令；
- 负责人的 `Acceptance-Refs` 登记了它负责的每个场景；
- 终态场景**与规则行**在**该 owner 的** Evidence 表里也登记且状态一致；
- 每个任务的契约表**每一行**（含 RULE 规则行）全终态；done 任务段落内零未勾项；
- spec-context 里每条 required 规则都有唯一负责人与可执行命令（argv 指向真实脚本）；
- E2E 命令指向真实在盘的套件与真实存在的场景名；**成功路径不得使用路由拦截**。

判定失败时消息里带上具体条目，便于直接定位到任务文件的那一行。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT / ".code-flow/tasks/archived/2026-09-17/13-console-auth"
TASK_FILE = TASK_DIR / "13-console-auth.md"
MANIFEST = TASK_DIR / ".acceptance-manifest.json"
SPEC_CONTEXT = TASK_DIR / "spec-context.yml"
E2E_SPEC = ROOT / "e2e/tests/console-auth.spec.ts"

CLOSURE_TASK = "TASK-012"
SCENARIO_ROW = re.compile(r"^(S|E|B)-\d+$")
RULE_ROW = re.compile(r"^RULE-[\w-]+$")
TERMINAL = {"verified", "e2e_deferred"}
DONE_STATUS = {"done", "verified"}


def _read(path: Path) -> str:
    assert path.exists(), f"缺少文件：{path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _normalize_status(cell: str) -> str:
    """状态单元格取首个 token：E2E 行常写成 `e2e_deferred（本地已 GREEN，终验归 verify-e2e）`。"""
    return re.split(r"[（(]", cell.strip())[0].strip()


def _table(block: str, first_cell: str) -> list[list[str]]:
    """取以 `first_cell` 开头的 markdown 表格数据行（跳过表头/分隔行）。"""
    rows: list[list[str]] = []
    for line in block.splitlines():
        if not line.startswith("|"):
            continue
        cells = _cells(line)
        if cells and re.match(first_cell, cells[0]):
            rows.append(cells)
    return rows


def _section(block: str, heading: str) -> str:
    """取任务段落里某个 `### <heading>` 小节（到下一个 `###` 为止）。

    契约表与证据表都带同样的场景 ID，必须按小节取，否则「证据登记」检查会被契约表蒙混过关。
    """
    marker = f"### {heading}"
    assert marker in block, f"缺少小节 {marker}"
    tail = block[block.index(marker) + len(marker) :]
    return tail.split("\n### ")[0]


def _task_blocks() -> dict[str, str]:
    text = _read(TASK_FILE)
    parts = re.split(r"^## (TASK-\d+): ", text, flags=re.MULTILINE)
    blocks: dict[str, str] = {}
    for index in range(1, len(parts), 2):
        blocks[parts[index]] = parts[index + 1]
    assert blocks, "未解析到任何 TASK 段落"
    return blocks


def _header() -> str:
    return _read(TASK_FILE).split("## TASK-001")[0]


def _coverage_rows() -> list[dict[str, str]]:
    rows = _table(_header(), r"(S|E|B|RULE)-")
    assert rows, "未解析到覆盖表"
    return [
        {"id": cells[0], "level": cells[2], "owner": cells[4], "status": cells[5], "argv": cells[6]}
        for cells in rows
    ]


def _acceptance_refs(block: str) -> set[str]:
    match = re.search(r"^- \*\*Acceptance-Refs\*\*: (.+)$", block, re.MULTILINE)
    assert match, "缺少 Acceptance-Refs"
    return {item.strip() for item in match.group(1).split(",") if item.strip()}


def _status(block: str) -> str:
    match = re.search(r"^- \*\*Status\*\*: (\S+)", block, re.MULTILINE)
    assert match, "缺少 Status"
    return match.group(1)


def _manifest() -> dict[str, Any]:
    return json.loads(_read(MANIFEST))


def _manifest_by_id() -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in _manifest()["scenarios"]}


def _e2e_tests() -> dict[str, str]:
    """E2E 套件里的 `test('…')` 块：场景 ID → 用例体。"""
    text = _read(E2E_SPEC)
    parts = re.split(r"^test\('([^']+)'", text, flags=re.MULTILINE)
    tests: dict[str, str] = {}
    for index in range(1, len(parts), 2):
        name = parts[index]
        tests[name] = parts[index + 1]
    assert tests, "未解析到 E2E 用例"
    return tests


def test_coverage_rows_have_exactly_one_owner() -> None:
    """覆盖表每个场景 ID 只出现一次，且负责人是文件内真实存在的 TASK。"""
    rows = _coverage_rows()
    known = set(_task_blocks())
    seen: dict[str, int] = {}
    for row in rows:
        seen[row["id"]] = seen.get(row["id"], 0) + 1
    duplicated = sorted(key for key, count in seen.items() if count > 1)
    assert not duplicated, f"覆盖表出现重复场景 ID：{duplicated}"

    unknown = sorted({row["owner"] for row in rows} - known)
    assert not unknown, f"覆盖表负责人不是本需求的 TASK：{unknown}"

    # 非空过：三类行都要在，解析失效时立刻暴露
    assert sum(bool(SCENARIO_ROW.match(row["id"])) for row in rows) >= 32
    assert sum(bool(RULE_ROW.match(row["id"])) for row in rows) >= 12


def test_coverage_rows_are_terminal() -> None:
    """覆盖表**每一行**都终态——含规则行与收口任务自己的行。

    规则行不是装饰：runner 只回写 S/E/B 行，RULE 行须人工终态化，
    否则「该规则已验收」在盘面上无从体现（12-overview B-209 的同类缺口）。
    """
    pending = [
        f"{row['id']}（{row['owner']}）={row['status']}"
        for row in _coverage_rows()
        if row["status"] not in TERMINAL
    ]
    assert not pending, f"覆盖表行未终态：{pending}"


def test_manifest_matches_coverage_table() -> None:
    """manifest 与覆盖表同 ID、同 owner、同执行命令。"""
    coverage = {row["id"]: row for row in _coverage_rows()}
    manifest = _manifest_by_id()
    assert set(manifest) <= set(coverage), f"manifest 多出场景：{sorted(set(manifest) - set(coverage))}"

    for scenario_id, item in sorted(manifest.items()):
        row = coverage[scenario_id]
        assert item["owner"] == row["owner"], (
            f"{scenario_id} owner 不一致：manifest={item['owner']} 覆盖表={row['owner']}"
        )
        assert json.loads(row["argv"]) == item["command"], (
            f"{scenario_id} 命令不一致：\n  manifest={item['command']}\n  覆盖表={row['argv']}"
        )


def test_owner_acceptance_refs_cover_its_scenarios() -> None:
    """每个负责人的 `Acceptance-Refs` 必须登记它负责的全部场景 ID。"""
    blocks = _task_blocks()
    by_owner: dict[str, set[str]] = {}
    for row in _coverage_rows():
        by_owner.setdefault(row["owner"], set()).add(row["id"])

    for owner, scenario_ids in sorted(by_owner.items()):
        declared = _acceptance_refs(blocks[owner])
        missing = sorted(scenario_ids - declared)
        assert not missing, f"{owner} 的 Acceptance-Refs 漏登记：{missing}"


def test_terminal_rows_are_registered_in_owner_evidence() -> None:
    """终态的**每一行**（含 RULE 规则行）都必须在其 owner 的 Evidence 表里登记，且状态一致。

    比 11-audit / 12-overview 的既有口径更严一档：那两份只查场景行，规则行只查终态。
    这里连规则行也要求有**自己的** Evidence 行——否则「规则已验收」只体现在覆盖表一个
    状态字上，TASK-009 的 RULE-data-001 就曾把 GREEN 挂在 B-07 行里、自己没有行。
    """
    blocks = _task_blocks()
    problems: list[str] = []
    for row in _coverage_rows():
        if row["status"] not in TERMINAL:
            continue
        evidence = {
            cells[0]: cells[-1]
            for cells in _table(_section(blocks[row["owner"]], "Acceptance Evidence"), r"(S|E|B|RULE)-")
        }
        status = evidence.get(row["id"])
        if status is None:
            problems.append(f"{row['id']} 未出现在 {row['owner']} 的 Evidence 表")
        elif _normalize_status(status) not in TERMINAL:
            problems.append(f"{row['id']} 在 {row['owner']} 的 Evidence 表状态为 {status}")
    assert not problems, f"Evidence 登记缺口：{problems}"


def test_contract_rows_are_terminal() -> None:
    """每个任务的契约表**每一行**必须终态——含规则行（与 11/12 的收口口径一致）。"""
    problems: list[str] = []
    for task_id, block in sorted(_task_blocks().items()):
        for cells in _table(_section(block, "Acceptance Contract"), r"(S|E|B|RULE)-"):
            status = _normalize_status(cells[-1])
            if status not in TERMINAL:
                problems.append(f"{task_id}:{cells[0]}={cells[-1]}")
    assert not problems, f"契约行未终态：{problems}"


def test_done_tasks_have_no_unchecked_items() -> None:
    """状态为 done/verified 的任务段落内不得残留未勾选项。"""
    problems: list[str] = []
    for task_id, block in sorted(_task_blocks().items()):
        if _status(block) not in DONE_STATUS:
            continue
        unchecked = [line.strip() for line in block.splitlines() if line.strip().startswith("- [ ]")]
        if unchecked:
            problems.append(f"{task_id}: {len(unchecked)} 项未勾（{unchecked[0][:60]}…）")
    assert not problems, f"done 任务存在未勾项：{problems}"


def _required_rules() -> set[str]:
    """spec-context 里 enforcement=required 的规则（本需求的权威规则集）。"""
    payload = yaml.safe_load(_read(SPEC_CONTEXT))
    refs: set[str] = set()
    for binding in payload.get("bindings", []):
        if binding.get("enforcement") != "required":
            continue
        refs |= {rule["ref"] for rule in binding.get("rules", []) if rule.get("enforcement") == "required"}
    assert refs, "spec-context 未解析到 required 规则"
    return refs


def test_required_rules_have_unique_owner_and_executable_command() -> None:
    """每条 required 规则都要在覆盖表里有唯一负责人，且 argv 指向真实存在的脚本/套件。"""
    coverage = {row["id"]: row for row in _coverage_rows() if RULE_ROW.match(row["id"])}
    required = _required_rules()
    missing = sorted(required - set(coverage))
    assert not missing, f"required 规则在覆盖表缺登记：{missing}"

    seen: dict[str, int] = {}
    for row in _coverage_rows():
        if RULE_ROW.match(row["id"]):
            seen[row["id"]] = seen.get(row["id"], 0) + 1
    duplicated = sorted(key for key, count in seen.items() if count > 1)
    assert not duplicated, f"规则行重复：{duplicated}"

    for rule_id in sorted(required):
        row = coverage[rule_id]
        assert row["owner"] in _task_blocks(), f"{rule_id} 负责人未知：{row['owner']}"
        argv = json.loads(row["argv"])
        assert isinstance(argv, list) and argv, f"{rule_id} 缺少可执行命令"
        for token in argv:
            if isinstance(token, str) and token.endswith((".py", ".ts", ".json")):
                path = (ROOT / token.split()[-1]).resolve()
                assert path.exists(), f"{rule_id} 命令引用的文件不存在：{token}"


def test_e2e_commands_point_to_real_suites_and_scenarios() -> None:
    """E2E 命令必须指向真实在盘的套件，且 `-g` 的场景 ID 在套件里真实存在。"""
    tests = _e2e_tests()
    checked = 0
    for row in _coverage_rows():
        argv = json.loads(row["argv"])
        command = " ".join(argv) if isinstance(argv, list) else str(argv)
        if "playwright test" not in command:
            continue
        checked += 1
        config = re.search(r"--config (\S+)", command)
        assert config, f"{row['id']} 未指定 --config"
        assert (ROOT / "e2e" / config.group(1)).exists(), f"{row['id']} 的 config 不在盘上"

        grep = re.search(r'-g "([^"]+)"', command)
        assert grep, f"{row['id']} 缺少 -g 场景过滤"
        matched = [name for name in tests if re.search(rf"^{grep.group(1)}[ ：]", name)]
        assert matched, f"{row['id']} 的 -g '{grep.group(1)}' 在 E2E 套件里没有对应用例"
    assert checked >= 10, f"E2E 命令解析疑似失效，只检查到 {checked} 条"


def test_success_paths_do_not_intercept_routes() -> None:
    """成功路径（S-*）不得用路由拦截制造结果——异常路径（E-*）才允许。"""
    tests = _e2e_tests()
    problems: list[str] = []
    for row in _coverage_rows():
        if not row["id"].startswith("S-"):
            continue
        matched = [
            (name, body)
            for name, body in tests.items()
            if re.search(rf"^{row['id']}[ ：]", name)
        ]
        if not matched:
            continue  # 非 playwright 场景（如前端 unit）由其它命令覆盖
        for name, body in matched:
            if "page.route(" in body:
                problems.append(f"{row['id']} → {name}")
    assert not problems, f"成功路径出现路由拦截：{problems}"
