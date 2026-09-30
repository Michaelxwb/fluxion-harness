"""[收口] 14-dfx-acceptance 需求级闭合清单（TASK-013 / RULE-test-001 的承接面）。

以**真实盘面**为输入做闭合核对，而不是自查断言：任务文件的覆盖表/契约表/证据表/checkbox、
验收 manifest、`spec-context.yml` 的 required 规则、design 的 `## Spec Compliance Matrix`，
以及每条命令引用的套件与用例是否真的在盘。

闭合口径（比 11-audit-observability / 12-overview-dashboard / 13-console-auth 更严一档：
那三份豁免「收口任务自身」，而「收口任务自己没终态」正是 13-console-auth 归档时暴露的静默缺口）：
- 覆盖表**每一行**（27 个场景 + 10 条 required 规则 = 37 行）唯一负责人且**全部终态**——
  含本收口任务的 S-13 与 RULE-test-001 两行；
- manifest 与覆盖表同 ID、同层级、同真实边界、同 owner、同命令、同 cwd（manifest 只登记场景行）；
- 终态场景**与规则行**在**该 owner 的** Evidence 小节里也登记且状态一致，不得残留占位行。
  manual 行的证据由 `cf_acceptance_manifest.py --record-manual` 写成小节内的
  `- <ID>: verified — … (confirmed_by: <人>)` 条目（工具形态），故 manual 接受条目形态，
  其余行要求有同 ID 的证据表行；
- 每个任务的契约表**每一行**（含 RULE 规则行）全终态；done 任务段落内零未勾项；
- 覆盖表的 RULE 行 == design `Spec Compliance Matrix` 的 required 规则（10 条），各有唯一负责人
  与可执行命令；`spec-context.yml` 继承的 required 规则必须是该 10 条的**超集**（多出的
  front/i18n/im/platform/time/ui 是 path-mapped 绑定，由各自域需求负责，本需求不建 RULE 行）；
- 每行 argv 引用的文件/目录在盘；E2E 行还须 `-k` 令牌命中真实用例名；
  RULE-test-001 的仓库级重链三段（acceptance / 前端 build / Playwright e2e）真实在盘；
- manual 行不得带自动 argv（manifest `command` 为 None），终态时必须留下人工确认人署名。

人工纪律（无脚本约束，只登记不机检）：
- 门禁/验收命令的输出不得接入 `| head` 一类会提前关闭的管道：SIGPIPE 会打断 pytest 收尾并留下
  共用同一测试库的孤儿服务进程（`muad_*.main`、`*_probe_app`），导致后续运行随机失败（失败点每次
  不同、单跑却通过）。每次运行前按 PPID=1 判据确认无残留进程。
- `docs/09 §14` 的 SCA/Mend 与 Image Scan 是 CI 外部工具报告，本仓不复现扫描（`.github/workflows/check.yml`
  只有 backend/frontend 两个 job，`deploy/` 仅 k8s 清单）；其证据路径由 TASK-013 的 S-13 门禁核对表登记。

判定失败时消息里带上具体条目，便于直接定位到任务文件的那一行。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
TASK_REL = ".code-flow/tasks/2026-09-17/14-dfx-acceptance"
ARCHIVED_REL = ".code-flow/tasks/archived/2026-09-17/14-dfx-acceptance"
TASK_FILE_NAME = "14-dfx-acceptance.md"
DESIGN_FILE_NAME = "14-dfx-acceptance.backend.design.md"

CLOSURE_TASK = "TASK-013"
MANUAL_LEVEL = "manual"
SCENARIO_ROW = re.compile(r"^(S|E|B)-\d+$")
RULE_ROW = re.compile(r"^RULE-[\w-]+$")
FULL_RULE_REF = re.compile(r"^`([\w-]+)#(RULE-[\w-]+)`$")
E2E_LEVEL = "E2E"
TERMINAL = {"verified", "e2e_deferred"}
DONE_STATUS = {"done", "verified"}
PLACEHOLDER = ("编码期填写", "待填", "TODO", "TBD")
EXPECTED_SCENARIOS = 27
EXPECTED_RULES = 10
PYTEST_FILES = (".py", ".ts", ".json")


def _dir() -> Path:
    """需求目录：未归档用 live 路径，归档后自动切到 archived（`/cf-task:archive` 会移动目录）。"""
    live = ROOT / TASK_REL
    archived = ROOT / ARCHIVED_REL
    assert live.exists() or archived.exists(), f"需求目录不存在：{TASK_REL} / {ARCHIVED_REL}"
    return live if live.exists() else archived


def _read(path: Path) -> str:
    assert path.exists(), f"缺少文件：{path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _normalize_status(cell: str) -> str:
    """状态单元格取首个 token：E2E 行常写成 `e2e_deferred（终验归 verify-e2e）`。"""
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
    text = _read(_dir() / TASK_FILE_NAME)
    parts = re.split(r"^## (TASK-\d+): ", text, flags=re.MULTILINE)
    blocks: dict[str, str] = {}
    for index in range(1, len(parts), 2):
        blocks[parts[index]] = parts[index + 1]
    assert blocks, "未解析到任何 TASK 段落"
    return blocks


def _header() -> str:
    return _read(_dir() / TASK_FILE_NAME).split("## TASK-001")[0]


def _coverage_rows() -> list[dict[str, str]]:
    rows = _table(_header(), r"(S|E|B|RULE)-")
    assert rows, "未解析到覆盖表"
    return [
        {
            "id": cells[0],
            "level": cells[2],
            "boundary": cells[3],
            "owner": cells[4],
            "status": _normalize_status(cells[5]),
            "argv": cells[6],
            "cwd": cells[7],
        }
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


def _manifest_by_id() -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in json.loads(_read(_dir() / ".acceptance-manifest.json"))["scenarios"]}


def _argv(row: dict[str, str]) -> list[str] | None:
    """覆盖表的 argv 单元格：manual 行写 `-`，其余是 JSON 数组。"""
    if row["argv"] == "-":
        return None
    parsed = json.loads(row["argv"])
    assert isinstance(parsed, list) and parsed, f"{row['id']} 的 argv 不是非空数组"
    return parsed


def _evidence_section(block: str) -> str:
    return _section(block, "Acceptance Evidence")


def _evidence_status(block: str, scenario_id: str, *, allow_entry: bool) -> str | None:
    """证据登记状态：`| ID | … | 状态 |` 表行优先。

    manual 行另接受 `- ID: 状态 — …` 条目（`--record-manual` 写入的工具形态）；
    **其余行不接受条目**——runner 会为每个跑过的场景写一条 `- ID: <status> — …`，
    若在这里也认条目，「规则行没有自己的证据行」就会被自动条目掩盖（13-console-auth
    的 RULE-data-001 正是把 GREEN 挂在别人的行里）。
    """
    for cells in _table(_evidence_section(block), r"(S|E|B|RULE)-"):
        if cells[0] == scenario_id:
            return _normalize_status(cells[-1])
    if not allow_entry:
        return None
    match = re.search(rf"^- {re.escape(scenario_id)}: (\S+) —", _evidence_section(block), re.MULTILINE)
    return match.group(1) if match else None


def test_coverage_rows_have_exactly_one_owner() -> None:
    """覆盖表每个场景 ID 只出现一次，负责人是文件内真实存在的 TASK，且三类行齐备。"""
    rows = _coverage_rows()
    known = set(_task_blocks())
    seen: dict[str, int] = {}
    for row in rows:
        seen[row["id"]] = seen.get(row["id"], 0) + 1
    duplicated = sorted(key for key, count in seen.items() if count > 1)
    assert not duplicated, f"覆盖表出现重复场景 ID：{duplicated}"

    unknown = sorted({row["owner"] for row in rows} - known)
    assert not unknown, f"覆盖表负责人不是本需求的 TASK：{unknown}"

    # 非空过：27 个场景 + 10 条规则行，解析失效时立刻暴露
    scenarios = sum(bool(SCENARIO_ROW.match(row["id"])) for row in rows)
    rules = sum(bool(RULE_ROW.match(row["id"])) for row in rows)
    assert scenarios == EXPECTED_SCENARIOS, f"覆盖表场景行应为 {EXPECTED_SCENARIOS} 行，实为 {scenarios}"
    assert rules == EXPECTED_RULES, f"覆盖表规则行应为 {EXPECTED_RULES} 行，实为 {rules}"


def test_coverage_rows_are_terminal() -> None:
    """覆盖表**每一行**都终态——含规则行，也含本收口任务自己的两行。

    规则行不是装饰：runner 只回写 S/E/B 行，RULE 行须人工终态化。豁免「收口任务自身」
    会让「收口任务的两行永远停在 planned」静默通过——13-console-auth 归档时就栽在这类收窄上。
    """
    pending = [
        f"{row['id']}（{row['owner']}）={row['status']}"
        for row in _coverage_rows()
        if row["status"] not in TERMINAL
    ]
    assert not pending, f"覆盖表行未终态：{pending}"


def test_manifest_matches_coverage_table() -> None:
    """manifest 与覆盖表同 ID、同层级、同真实边界、同 owner、同命令、同 cwd。"""
    coverage = {row["id"]: row for row in _coverage_rows()}
    manifest = _manifest_by_id()
    assert set(manifest) <= set(coverage), f"manifest 多出场景：{sorted(set(manifest) - set(coverage))}"

    for scenario_id, item in sorted(manifest.items()):
        row = coverage[scenario_id]
        assert item["level"] == row["level"], (
            f"{scenario_id} 层级不一致：manifest={item['level']} 覆盖表={row['level']}"
        )
        assert item["boundary"] == row["boundary"], (
            f"{scenario_id} 真实边界不一致：\n  manifest={item['boundary']}\n  覆盖表={row['boundary']}"
        )
        assert item["owner"] == row["owner"], (
            f"{scenario_id} owner 不一致：manifest={item['owner']} 覆盖表={row['owner']}"
        )
        assert item["cwd"] == row["cwd"], (
            f"{scenario_id} cwd 不一致：manifest={item['cwd']} 覆盖表={row['cwd']}"
        )
        assert item["command"] == _argv(row), (
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
    """终态的**每一行**（含 RULE 规则行）都要在其 owner 的 Evidence 小节里登记，且状态一致。"""
    blocks = _task_blocks()
    problems: list[str] = []
    for row in _coverage_rows():
        if row["status"] not in TERMINAL:
            continue
        block = blocks[row["owner"]]
        status = _evidence_status(block, row["id"], allow_entry=row["level"] == MANUAL_LEVEL)
        if status is None:
            problems.append(f"{row['id']} 未出现在 {row['owner']} 的 Evidence 小节")
        elif status not in TERMINAL:
            problems.append(f"{row['id']} 在 {row['owner']} 的 Evidence 状态为 {status}")
    assert not problems, f"Evidence 登记缺口：{problems}"


def test_evidence_tables_have_no_placeholders() -> None:
    """证据表不得残留占位行——「未执行」不得冒充通过，占位符留着就可能被当成已登记。"""
    problems: list[str] = []
    for task_id, block in sorted(_task_blocks().items()):
        for line in _evidence_section(block).splitlines():
            if line.startswith("|") and any(token in line for token in PLACEHOLDER):
                problems.append(f"{task_id}: {line.strip()[:60]}…")
    assert not problems, f"证据表残留占位行：{problems}"


def test_contract_rows_are_terminal() -> None:
    """每个任务的契约表**每一行**必须终态——含规则行与 manual 行。"""
    problems: list[str] = []
    for task_id, block in sorted(_task_blocks().items()):
        for cells in _table(_section(block, "Acceptance Contract"), r"(S|E|B|RULE)-"):
            if _normalize_status(cells[-1]) not in TERMINAL:
                problems.append(f"{task_id}:{cells[0]}={cells[-1]}")
    assert not problems, f"契约行未终态：{problems}"


def test_contract_tables_cover_every_acceptance_ref() -> None:
    """每个任务的契约表要为它 `Acceptance-Refs` 里的**每个** ID 都留一行。

    只查「行是否终态」会漏掉「整行被删」：行没了就没有非终态状态可查，静默通过。
    """
    problems: list[str] = []
    for task_id, block in sorted(_task_blocks().items()):
        rows = {cells[0] for cells in _table(_section(block, "Acceptance Contract"), r"(S|E|B|RULE)-")}
        missing = sorted(_acceptance_refs(block) - rows)
        if missing:
            problems.append(f"{task_id} 契约表缺行：{missing}")
    assert not problems, f"契约表覆盖缺口：{problems}"


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


def _design_required_rules() -> set[str]:
    """design `## Spec Compliance Matrix` 里 enforcement=required 的规则（本需求的权威规则集）。"""
    refs: set[str] = set()
    for line in _read(_dir() / DESIGN_FILE_NAME).splitlines():
        if not line.startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) < 2 or cells[1] != "required":
            continue
        matched = FULL_RULE_REF.match(cells[0])
        if matched:
            refs.add(matched.group(2))
    assert refs, "未从 design Spec Compliance Matrix 解析到 required 规则"
    return refs


def _spec_context_required_rules() -> set[str]:
    """`spec-context.yml` 里 enforcement=required 的短规则名（含 path-mapped 自动绑定）。"""
    payload = yaml.safe_load(_read(_dir() / "spec-context.yml"))
    refs: set[str] = set()
    for binding in payload.get("bindings", []):
        if binding.get("enforcement") != "required":
            continue
        refs |= {rule["ref"] for rule in binding.get("rules", []) if rule.get("enforcement") == "required"}
    assert refs, "spec-context 未解析到 required 规则"
    return refs


def test_rule_rows_match_design_matrix() -> None:
    """覆盖表的 RULE 行必须**恰好**是 design 矩阵的 required 规则，各有唯一负责人与非空 argv。"""
    coverage = {row["id"]: row for row in _coverage_rows() if RULE_ROW.match(row["id"])}
    required = _design_required_rules()
    missing = sorted(required - set(coverage))
    extra = sorted(set(coverage) - required)
    assert set(coverage) == required, f"RULE 行与 design 矩阵不符：缺 {missing}，多 {extra}"
    assert len(coverage) == EXPECTED_RULES, f"RULE 行应为 {EXPECTED_RULES} 行"

    # spec-context 的 required 是 design 矩阵的超集：多出的是 path-mapped 绑定，由各自域需求负责
    inherited = _spec_context_required_rules()
    assert required <= inherited, f"design 矩阵的规则未出现在 spec-context：{sorted(required - inherited)}"

    blocks = _task_blocks()
    for rule_id, row in sorted(coverage.items()):
        assert row["owner"] in blocks, f"{rule_id} 负责人未知：{row['owner']}"
        assert _argv(row) is not None, f"{rule_id} 缺少可执行命令（规则行不得是 manual）"
        refs = _acceptance_refs(blocks[row["owner"]])
        assert rule_id in refs, f"{rule_id} 未登记在 {row['owner']} 的 Acceptance-Refs"


def _legs(argv: list[str]) -> list[str]:
    """把 argv 还原成可执行命令行并按 `&&` 拆段（本需求的复合命令写成 `bash -lc "a && b"`）。"""
    if len(argv) >= 2 and argv[0] == "bash" and argv[1] in ("-lc", "-c"):
        return [leg.strip() for leg in " ".join(argv[2:]).split("&&") if leg.strip()]
    return [" ".join(argv)]


def _referenced_paths(leg: str) -> list[str]:
    """命令行里形如仓库路径的 token（`--flag=path` 取等号右侧；`-k <token>` 之类跳过）。"""
    paths: list[str] = []
    for raw in leg.split():
        token = raw.strip("\"'")
        if token.startswith("-"):
            if "=" not in token:
                continue
            token = token.split("=", 1)[1]
        if token.endswith(PYTEST_FILES) or "/" in token:
            paths.append(token)
    return paths


def test_commands_reference_paths_on_disk() -> None:
    """每一行的 argv（含 `bash -lc` 多段链）引用的文件/目录必须在盘。"""
    checked = 0
    for row in _coverage_rows():
        argv = _argv(row)
        if argv is None:
            continue
        for leg in _legs(argv):
            for path in _referenced_paths(leg):
                checked += 1
                assert (ROOT / path).exists(), f"{row['id']} 命令引用的路径不存在：{path}"
    assert checked >= 40, f"路径解析疑似失效，只检查到 {checked} 条"


def test_rule_commands_are_executable() -> None:
    """每条 required 规则的 argv 必须是非空可执行命令（命令首段真实可解析）。"""
    for row in _coverage_rows():
        if not RULE_ROW.match(row["id"]):
            continue
        argv = _argv(row)
        assert argv is not None, f"{row['id']} 缺少 argv"
        legs = _legs(argv)
        assert legs, f"{row['id']} argv 拆不出可执行段"
        head = legs[0].split()[0]
        assert head in {"uv", "npm", "bash", "python3"}, f"{row['id']} argv 首段不可识别：{legs[0]}"


def test_e2e_rows_point_to_real_suites_and_cases() -> None:
    """E2E 行（含 RULE-test-001 的仓库级重链）必须指向真实在盘的套件与真实存在的用例名。"""
    tests = 0
    for row in _coverage_rows():
        if row["level"] != E2E_LEVEL:
            continue
        argv = _argv(row)
        assert argv is not None, f"{row['id']} 是 E2E 行却没有 argv"
        if row["id"] == "RULE-test-001":
            _assert_rule_test_chain(argv)
            tests += 1
            continue
        for leg in _legs(argv):
            tokens = leg.split()
            key = [tokens[i + 1].strip("\"'") for i, token in enumerate(tokens[:-1]) if token == "-k"]
            source = " ".join(_referenced_paths(leg))
            files = sorted((ROOT / path) for path in _referenced_paths(leg) if path.endswith(".py"))
            assert files, f"{row['id']} 的 E2E 段未引用任何 pytest 文件：{leg}"
            for token in key:
                pattern = rf"def test_\w*{re.escape(token)}\w*"
                matched = [f for f in files if re.search(pattern, f.read_text(encoding="utf-8"))]
                assert matched, f"{row['id']} 的 -k '{token}' 在 {source} 里没有对应用例"
            tests += 1
    assert tests >= 8, f"E2E 行解析疑似失效，只检查到 {tests} 条"


def _assert_rule_test_chain(argv: list[str]) -> None:
    """RULE-test-001 的仓库级重链三段：acceptance 套件 / 前端真实构建 / Playwright 真实浏览器。"""
    legs = _legs(argv)
    assert len(legs) == 3, f"RULE-test-001 应为三段重链，实为 {legs}"
    acceptance, frontend, browser = legs

    acceptance_dir = ROOT / "tests/acceptance"
    suites = sorted(acceptance_dir.glob("test_*.py")) + sorted(acceptance_dir.glob("*/*.py"))
    assert "tests/acceptance" in acceptance, f"acceptance 段未指向真实套件：{acceptance}"
    assert len(suites) >= 20, f"acceptance 段套件过少：{len(suites)}"

    frontend_pkg = json.loads(_read(ROOT / "apps/console-platform/frontend/package.json"))
    assert "run build" in frontend, f"前端段没有真实 build：{frontend}"
    assert "build" in frontend_pkg.get("scripts", {}), "前端 package.json 没有 build 脚本"

    e2e_pkg = json.loads(_read(ROOT / "e2e/package.json"))
    specs = sorted((ROOT / "e2e/tests").rglob("*.spec.ts"))
    assert "e2e test" in browser, f"浏览器段没有真实 e2e：{browser}"
    assert e2e_pkg.get("scripts", {}).get("test") == "playwright test", "e2e 的 npm test 不是 Playwright"
    assert (ROOT / "e2e/playwright.config.ts").exists(), "e2e 默认配置不在盘"
    assert len(specs) >= 10, f"Playwright 套件过少：{len(specs)}"


def test_manual_rows_stay_manual_with_human_confirmation() -> None:
    """manual 行不得带自动 argv；终态时 manifest 同状态且留下人工确认人署名（agent 不得代签）。"""
    blocks = _task_blocks()
    manifest = _manifest_by_id()
    manual = [row for row in _coverage_rows() if row["level"] == MANUAL_LEVEL]
    assert manual, "覆盖表未解析到 manual 行"

    for row in manual:
        scenario_id = row["id"]
        item = manifest.get(scenario_id)
        assert item is not None, f"{scenario_id} 不在 manifest"
        assert row["argv"] == "-", f"{scenario_id} 是 manual 行却带自动 argv：{row['argv']}"
        assert item["command"] is None, f"{scenario_id} manifest 命令应为 null，实为 {item['command']}"
        if row["status"] in TERMINAL:
            assert item["status"] in TERMINAL, f"{scenario_id} manifest 状态={item['status']} 与覆盖表不一致"
            section = _evidence_section(blocks[row["owner"]])
            assert "confirmed_by:" in section, f"{scenario_id} 终态却没有人工确认署名（confirmed_by）"
