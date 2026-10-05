"""[收口] platform-settings 需求级闭合清单（TASK-012 / E-18 / RULE-test-001 的承接面）。

以**真实盘面**为输入做闭合核对，而不是自查断言：任务文件的覆盖表/契约表/证据表/勾选清单、
`.acceptance-manifest.json`、每条登记命令引用的文件与 `-k` 令牌是否真的在盘。

闭合口径（承接 context-compaction 的「连收口任务自身也不豁免」）：
- 覆盖表**每一行**（34 条场景）唯一负责人且终态——本清单不为收口任务 TASK-012 开任何豁免；
- manifest 与覆盖表同 ID、同 `level`/`boundary`/`cwd`/`timeout`、同 owner、同命令；
- 终态场景行**与终态 RULE 规则行**必须在**该 owner 的** `### Acceptance Evidence` 里有同 ID 的
  **证据表行**（不接受 runner 自动写的 `- <ID>: <status> — …` 条目形态：那会让"规则行没有自己的
  证据行"被自动条目掩盖）；
- 每个任务的契约表**每一行**（含 RULE 行）全终态；**且** `Acceptance-Refs` 里每个 ID 都有一行
  契约——只查"行是否终态"查不出"整行被删"（行没了就没有非终态状态可查），故必须另立断言；
- 每个任务的 `### Checklist` **全部勾选**（`- [ ]` 归零）；
- 证据表不得残留占位行；
- 每条登记命令（覆盖表 + 各任务契约表的 `执行命令` 列）引用的文件/目录在盘；命令里的 `-k`
  令牌必须在该 leg 引用的真实测试文件里命中真实用例名。

结构性 RED（本清单自身的判定基准）：登记命令路径 `tests/platform_settings_inventory.py` 缺失时，
按该 argv 执行即失败——本文件存在性由 `test_inventory_registers_its_own_command_path` 兜住。

判定失败时消息带上具体条目，便于直接定位到任务文件的那一行。
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TASK_REL = ".code-flow/tasks/2026-10-04/platform-settings"
ARCHIVED_GLOB = ".code-flow/tasks/archived/*/platform-settings"
TASK_FILE_NAME = "platform-settings.md"
MANIFEST_NAME = ".acceptance-manifest.json"

INVENTORY_REL = "tests/platform_settings_inventory.py"

SCENARIO_ROW = re.compile(r"^[SEB]-\d+$")
RULE_ROW = re.compile(r"^RULE-[\w-]+$")
ROW = re.compile(r"^[SEB]-\d+$|^RULE-[\w-]+$")
TERMINAL = {"verified", "e2e_deferred"}
PLACEHOLDER = ("编码期填写", "待填", "TODO", "TBD")
PLACEHOLDER_STATUS = {"planned", "pending", "tbd", "todo", "待填"}
PATH_SUFFIXES = (".py", ".ts", ".json")
KEY_TOKENS = ("-k", "-g")
UNCHECKED = re.compile(r"^\s*- \[ \]", re.MULTILINE)
EXPECTED_SCENARIOS = 34
EXPECTED_TASKS = 17


def _dir() -> Path:
    """需求目录：未归档用 live 路径，归档后自动切到 archived（`/cf-task:archive` 会移动目录）。"""
    live = ROOT / TASK_REL
    if live.exists():
        return live
    archived = sorted(ROOT.glob(ARCHIVED_GLOB))
    assert archived, f"需求目录不存在：{TASK_REL}（也未在 archived 下找到）"
    return archived[-1]


def _read(path: Path) -> str:
    assert path.exists(), f"缺少文件：{path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _normalize_status(cell: str) -> str:
    """状态单元格取首个 token：E2E 行常写成 `e2e_deferred（终验归 verify-e2e）`。"""
    return re.split(r"[（(]", cell.strip())[0].strip()


def _section(block: str, heading: str) -> str:
    """取任务段落里某个 `### <heading>` 小节（到下一个 `###` 为止）。"""
    marker = f"### {heading}"
    assert marker in block, f"缺少小节 {marker}"
    tail = block[block.index(marker) + len(marker) :]
    return tail.split("\n### ")[0]


def _table(block: str, row_pattern: re.Pattern[str]) -> list[list[str]]:
    """取 `row_pattern` 命中首格的 markdown 表格数据行（跳过表头/分隔行）。"""
    rows: list[list[str]] = []
    for line in block.splitlines():
        if not line.startswith("|"):
            continue
        cells = _cells(line)
        if cells and row_pattern.match(cells[0]):
            rows.append(cells)
    return rows


def _task_blocks() -> dict[str, str]:
    text = _read(_dir() / TASK_FILE_NAME)
    parts = re.split(r"^## (TASK-\d+): ", text, flags=re.MULTILINE)
    blocks: dict[str, str] = {}
    for index in range(1, len(parts), 2):
        blocks[parts[index]] = parts[index + 1]
    assert blocks, "未解析到任何 TASK 段落"
    return blocks


def _coverage_block() -> str:
    """覆盖表所在段落（`## Acceptance Coverage` 到下一个 `## `）。"""
    text = _read(_dir() / TASK_FILE_NAME)
    marker = "## Acceptance Coverage"
    assert marker in text, "缺少 Acceptance Coverage 段"
    tail = text[text.index(marker) + len(marker) :]
    return tail.split("\n## ")[0]


def _coverage_rows() -> list[dict[str, str]]:
    rows = _table(_coverage_block(), ROW)
    assert rows, "未解析到覆盖表"
    return [
        {
            "id": cells[0],
            "source": cells[1],
            "level": cells[2],
            "boundary": cells[3],
            "owner": cells[4],
            "status": _normalize_status(cells[5]),
            "command": cells[6],
            "cwd": cells[7],
            "timeout": cells[8],
        }
        for cells in rows
    ]


def _acceptance_refs(block: str) -> set[str]:
    """`Acceptance-Refs` 里的行 ID（过滤掉 `N/A` 一类非行记号）。"""
    match = re.search(r"^- \*\*Acceptance-Refs\*\*: (.+)$", block, re.MULTILINE)
    assert match, "缺少 Acceptance-Refs"
    return {item.strip() for item in match.group(1).split(",") if ROW.match(item.strip())}


def _contract_rows(block: str) -> list[tuple[str, str]]:
    return [
        (cells[0], _normalize_status(cells[-1]))
        for cells in _table(_section(block, "Acceptance Contract"), ROW)
    ]


def _evidence_rows(block: str) -> list[tuple[str, str]]:
    return [
        (cells[0], _normalize_status(cells[-1]))
        for cells in _table(_section(block, "Acceptance Evidence"), ROW)
    ]


def _manifest_items() -> list[dict[str, Any]]:
    payload = json.loads(_read(_dir() / MANIFEST_NAME))
    return list(payload["scenarios"])


def _argv(cell: str) -> list[str] | None:
    """命令单元格：`-` / `planned` 等未登记记号返回 None，其余按 argv（JSON 数组或 shell words）解析。"""
    text = (cell or "").strip()
    if not text or text in {"-", "planned", "pending", "TBD"}:
        return None
    if text.startswith("["):
        parsed = json.loads(text)
        assert isinstance(parsed, list) and parsed, f"命令列不是非空数组：{cell}"
        return list(parsed)
    return shlex.split(text)


def _shell_expand(leg: list[str]) -> list[list[str]]:
    """`bash -lc "<script>"` / `sh -c "<script>"` → 把脚本按 `&&` 拆成子 leg。

    本需求的 E2E 命令是 `["bash","-lc","<前端 build> && <playwright>"]` 形态（见 13-console-auth
    的同型登记）：脚本是**一个** token，不展开就只能当成一串带空白的伪路径。
    """
    for index, token in enumerate(leg):
        if token in ("-lc", "-c") and index + 1 < len(leg):
            script = " ".join(leg[index + 1 :])
            return [shlex.split(part) for part in script.split("&&") if part.strip()]
    return [leg]


def _legs(cell: str) -> list[list[str]]:
    """把命令单元格拆成若干 leg（每个 leg 一个 argv）。

    命令单元格有两种登记形态：JSON 数组（本需求覆盖表/契约表的登记形态）与 `&&` 连接的
    shell 串（规则 verifier 的联合命令）。`bash -lc "<script>"` 再把脚本展开为子 leg。
    """
    text = (cell or "").strip()
    if not text or text in {"-", "planned", "pending", "TBD"}:
        return []
    if text.startswith("["):
        parsed = json.loads(text)
        assert isinstance(parsed, list) and parsed, f"命令列不是非空数组：{cell}"
        base = [list(parsed)]
    else:
        base = [shlex.split(leg) for leg in text.split("&&") if leg.strip()]
    legs: list[list[str]] = []
    for leg in base:
        legs.extend(_shell_expand(leg))
    return legs


def _prefix_dir(argv: list[str]) -> str | None:
    """`npm --prefix <dir>` 的工作目录（用于把该 leg 里的相对路径解析到正确基点）。"""
    for index, raw in enumerate(argv[:-1]):
        if raw == "--prefix":
            return argv[index + 1]
    return None


def _referenced_paths(argv: list[str]) -> list[str]:
    """argv 里形如仓库路径的 token（`--flag=path` 取等号右侧；`-k`/`-g` 的取值跳过）。"""
    paths: list[str] = []
    skip_next = False
    for raw in argv:
        if skip_next:
            skip_next = False
            continue
        if raw in KEY_TOKENS:
            skip_next = True
            continue
        token = raw.strip("\"'")
        if token.startswith("-"):
            if "=" not in token:
                continue
            token = token.split("=", 1)[1]
        if token.endswith(PATH_SUFFIXES) or "/" in token:
            paths.append(token)
    return paths


def _registered_commands() -> list[tuple[str, str]]:
    """每条登记命令：覆盖表各行 + 每个任务契约表各行 → (行 ID, 命令单元格)。"""
    commands = [(row["id"], row["command"]) for row in _coverage_rows()]
    for block in _task_blocks().values():
        commands += [(cells[0], cells[-2]) for cells in _table(_section(block, "Acceptance Contract"), ROW)]
    assert commands, "未解析到任何登记命令"
    return commands


def test_inventory_registers_its_own_command_path() -> None:
    """清单自身的命令路径在盘且登记在任务文档里——结构性 RED 的判定基准。

    先跑登记的 argv（`uv run pytest -q tests/platform_settings_inventory.py`）应失败于
    `ERROR: file or directory not found`（exit=4）；本行把它收成一条常驻断言。
    """
    assert (ROOT / INVENTORY_REL).is_file(), f"清单自身命令路径不在盘：{INVENTORY_REL}"
    assert INVENTORY_REL in _read(_dir() / TASK_FILE_NAME), f"任务文档未登记清单命令路径：{INVENTORY_REL}"


def test_task_set_is_complete() -> None:
    """本需求的 17 个 TASK 段落都在盘——段落整段被删会静默绕过下方所有逐任务断言。"""
    blocks = _task_blocks()
    missing = sorted({f"TASK-{i:03d}" for i in range(1, EXPECTED_TASKS + 1)} - set(blocks))
    assert not missing, f"缺少 TASK 段落：{missing}"


def test_coverage_rows_have_exactly_one_owner() -> None:
    """覆盖表每个场景 ID 只出现一次，负责人是本需求真实存在的 TASK，且场景数与非空过达标。"""
    rows = _coverage_rows()
    known = set(_task_blocks())
    duplicated = sorted({row["id"] for row in rows if [r["id"] for r in rows].count(row["id"]) > 1})
    assert not duplicated, f"覆盖表出现重复场景 ID：{duplicated}"

    unknown = sorted({row["owner"] for row in rows} - known)
    assert not unknown, f"覆盖表负责人不是本需求的 TASK：{unknown}"

    scenarios = sum(bool(SCENARIO_ROW.match(row["id"])) for row in rows)
    assert scenarios == EXPECTED_SCENARIOS, f"覆盖表场景行应为 {EXPECTED_SCENARIOS} 行，实为 {scenarios}"
    assert all(row["level"] and row["boundary"] for row in rows), "覆盖表存在空层级/空边界行"


def test_coverage_rows_are_terminal() -> None:
    """覆盖表**每一行**都终态——本清单不为收口任务 TASK-012 开豁免。

    断言里也不写任何 `owner == TASK-012` 的短路，避免"收口任务没终态"静默通过。
    """
    pending = [
        f"{row['id']}（{row['owner']}）={row['status']}"
        for row in _coverage_rows()
        if row["status"] not in TERMINAL
    ]
    assert not pending, f"覆盖表行未终态：{pending}"


def test_manifest_matches_coverage_table() -> None:
    """manifest 与覆盖表同 ID、同 `level`/`boundary`/`cwd`/`timeout`、同 owner、同命令。"""
    coverage = {row["id"]: row for row in _coverage_rows()}
    manifest = {item["id"]: item for item in _manifest_items()}
    assert set(manifest) <= set(coverage), f"manifest 多出场景：{sorted(set(manifest) - set(coverage))}"
    assert set(coverage) == set(manifest), f"manifest 漏场景：{sorted(set(coverage) - set(manifest))}"

    for scenario_id, item in sorted(manifest.items()):
        row = coverage[scenario_id]
        assert item["owner"] == row["owner"], (
            f"{scenario_id} owner 不一致：manifest={item['owner']} 覆盖表={row['owner']}"
        )
        assert item["level"] == row["level"], (
            f"{scenario_id} 层级不一致：manifest={item['level']} 覆盖表={row['level']}"
        )
        assert item["boundary"] == row["boundary"], (
            f"{scenario_id} 真实边界不一致：\n  manifest={item['boundary']}\n  覆盖表={row['boundary']}"
        )
        assert item["cwd"] == row["cwd"], (
            f"{scenario_id} cwd 不一致：manifest={item['cwd']} 覆盖表={row['cwd']}"
        )
        assert float(item["timeout"]) == float(row["timeout"]), (
            f"{scenario_id} timeout 不一致：manifest={item['timeout']} 覆盖表={row['timeout']}"
        )
        assert item["command"] == _argv(row["command"]), (
            f"{scenario_id} 命令不一致：\n  manifest={item['command']}\n  覆盖表={_argv(row['command'])}"
        )


def test_all_checklists_are_fully_checked() -> None:
    """每个任务的 `### Checklist` 必须全部勾选（`- [ ]` 归零）——含收口任务自身。"""
    problems: list[str] = []
    for task_id, block in sorted(_task_blocks().items()):
        unchecked = UNCHECKED.findall(_section(block, "Checklist"))
        if unchecked:
            problems.append(f"{task_id} 有 {len(unchecked)} 项未勾选")
    assert not problems, f"Checklist 未全部勾选：{problems}"


def test_owner_acceptance_refs_cover_its_scenarios() -> None:
    """每个负责人的 `Acceptance-Refs` 必须登记它负责的全部场景 ID。"""
    blocks = _task_blocks()
    by_owner: dict[str, set[str]] = {}
    for row in _coverage_rows():
        by_owner.setdefault(row["owner"], set()).add(row["id"])

    for owner, scenario_ids in sorted(by_owner.items()):
        missing = sorted(scenario_ids - _acceptance_refs(blocks[owner]))
        assert not missing, f"{owner} 的 Acceptance-Refs 漏登记：{missing}"


def test_terminal_rows_are_registered_in_owner_evidence() -> None:
    """终态的**每一行**（含契约表里的 RULE 规则行）都要在 owner 的证据表里有同 ID 的一行。

    只认证据表行，不认 runner 自动写的 `- <ID>: <状态> — …` 条目：条目形态会掩盖"规则行没有
    自己的证据行"。
    """
    blocks = _task_blocks()
    owner_of_coverage = {row["id"]: row["owner"] for row in _coverage_rows()}
    problems: list[str] = []
    for task_id, block in sorted(blocks.items()):
        for row_id, status in _contract_rows(block):
            if status not in TERMINAL:
                continue
            owner = owner_of_coverage.get(row_id, task_id)
            evidence = dict(_evidence_rows(blocks[owner]))
            if row_id not in evidence:
                problems.append(f"{row_id}（owner={owner}，契约={status}）未出现在 {owner} 的 Evidence 小节")
            elif evidence[row_id] not in TERMINAL:
                problems.append(f"{row_id} 在 {owner} 的 Evidence 状态为 {evidence[row_id]}")
    assert not problems, f"Evidence 登记缺口：{problems}"


def test_evidence_tables_have_no_placeholder_rows() -> None:
    """证据表不得残留占位行——「未执行」不得冒充通过，占位符留着就可能被当成已登记。"""
    problems: list[str] = []
    for task_id, block in sorted(_task_blocks().items()):
        section = _section(block, "Acceptance Evidence")
        for line in section.splitlines():
            if line.startswith("|") and any(token in line for token in PLACEHOLDER):
                problems.append(f"{task_id}: {line.strip()[:60]}…")
        for row_id, status in _evidence_rows(block):
            if status in PLACEHOLDER_STATUS:
                problems.append(f"{task_id}:{row_id} 状态为占位值 {status}")
    assert not problems, f"证据表残留占位行：{problems}"


def test_contract_rows_are_terminal() -> None:
    """每个任务的契约表**每一行**必须终态——含 RULE 规则行，也含收口任务 TASK-012 自己的两行。"""
    problems: list[str] = []
    for task_id, block in sorted(_task_blocks().items()):
        for row_id, status in _contract_rows(block):
            if status not in TERMINAL:
                problems.append(f"{task_id}:{row_id}={status}")
    assert not problems, f"契约行未终态：{problems}"


def test_contract_tables_cover_every_acceptance_ref() -> None:
    """每个任务的契约表要为它 `Acceptance-Refs` 里的**每个** ID 都留一行。

    只查「行是否终态」会漏掉「整行被删」：行没了就没有非终态状态可查，静默通过。
    """
    problems: list[str] = []
    for task_id, block in sorted(_task_blocks().items()):
        rows = {row_id for row_id, _ in _contract_rows(block)}
        missing = sorted(_acceptance_refs(block) - rows)
        if missing:
            problems.append(f"{task_id} 契约表缺行：{missing}")
    assert not problems, f"契约表覆盖缺口：{problems}"


def test_registered_commands_reference_paths_on_disk() -> None:
    """每条登记命令（覆盖表 + 契约表）引用的文件/目录必须在盘。

    路径 token 默认相对仓库根解析；`npm --prefix <dir>` 的 leg 里，相对路径（如
    `playwright.settings.config.ts`）解析到 `<dir>` 之下。
    """
    checked = 0
    for row_id, command in _registered_commands():
        if _argv(command) is None:
            continue
        for leg in _legs(command):
            prefix = _prefix_dir(leg)
            for path in _referenced_paths(leg):
                checked += 1
                candidates = [ROOT / path]
                if prefix:
                    candidates.append(ROOT / prefix / path)
                assert any(candidate.exists() for candidate in candidates), (
                    f"{row_id} 命令引用的路径不存在：{path}"
                )
    assert checked >= 10, f"路径解析疑似失效，只检查到 {checked} 条"


def test_registered_commands_k_tokens_hit_real_cases() -> None:
    """命令里的 `-k`/`-g` 令牌必须在同一 leg 引用的真实测试文件里命中真实用例名。

    一旦有人把 `-k <伪造用例名>` 写进覆盖表或契约表的 `执行命令` 列，这条检查必须变红并指名条目。
    """
    commands = _registered_commands()
    assert len(commands) >= EXPECTED_SCENARIOS, f"命令解析疑似失效，只解析到 {len(commands)} 条登记命令"
    for row_id, command in commands:
        for leg in _legs(command):
            tokens = [leg[i + 1].strip("\"'") for i, raw in enumerate(leg[:-1]) if raw in KEY_TOKENS]
            if not tokens:
                continue
            files = [ROOT / path for path in _referenced_paths(leg) if path.endswith(".py")]
            assert files, f"{row_id} 的 leg 带 `-k` 却没引用任何 pytest 文件：{' '.join(leg)}"
            source = " ".join(path.read_text(encoding="utf-8") for path in files)
            for token in tokens:
                pattern = rf"def test_\w*{re.escape(token)}\w*"
                assert re.search(pattern, source), (
                    f"{row_id} 的 `-k {token}` 在 {' '.join(str(f) for f in files)} 里没有对应用例"
                )
