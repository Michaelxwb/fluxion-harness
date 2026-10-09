"""[收口] async-tool-runtime 需求级闭合清单（TASK-009 / harness-test#RULE-test-001 的承接面）。

以**真实盘面**为输入做交叉核对，而不是自查断言：任务文件的覆盖表/契约表/证据表、
`.acceptance-manifest.json`、每条登记命令引用的文件与 `-k`/`-g` 令牌是否真的在盘，
以及 E2E 场景名是否落在真实套件里。

闭合口径（B-09 契约逐条对应）：
- 覆盖表 43 条场景每行唯一负责人（真实存在的 TASK）、层级/边界非空、命令可执行且终态；
- manifest 与覆盖表同 ID 集合、同 source/level/boundary/owner/命令；
- 每个负责人的 `Acceptance-Refs` 覆盖它负责的全部场景；
- 终态契约行在该 owner 的 Evidence 小节登记且状态一致（表格行优先；本需求
  TASK-003/006/007/008 以 runner 条目为主要证据形态，条目可作登记形态，
  但删掉该行的全部登记必须变红——见扰动取证）；
- 证据表不得残留占位行；每个任务的契约表每一行终态，且为每条 `Acceptance-Refs` 留行
  （只查"行是否终态"查不出"整行被删"）；
- 每条登记命令引用的文件/目录在盘（含 `cd` 段与 `bash -lc` 内层命令），
  `-k`/`-g` 令牌必须在真实用例名里命中；integration 行必须引用具体 pytest 文件；
- E2E 行的场景 ID 必须在被引用套件里存在（pytest 用例名或 Playwright test 标题，
  域配置经 `testMatch` 解析到真实 spec）；
- E2E 隔离机制在盘：验收 conftest 自动建独立空库 + 独立 Redis 号位、Playwright 域配置
  走隔离 datastore（preview 真实构建产物、钉时区）、Makefile 先 build 再跑浏览器套件；
- 收口任务自身不豁免：TASK-009 的 B-09 也必须终态且登记。

结构性 RED + 扰动取证（B-09 契约）：清单文件缺失时登记的 argv 失败（`file or directory
not found`，exit=4）；四类扰动——改状态、删证据登记、伪造用例名、改 manifest 边界——
各自使对应检查变红且消息指名条目；**逐字节还原**后复跑全绿。扰动用字节备份 +
try/finally 还原，autouse 守卫在每条用例后比对任务文件与 manifest 的字节，确保不留扰动。

判定失败时消息带上具体条目，便于直接定位到任务文件的那一行。
"""

from __future__ import annotations

import json
import posixpath
import re
import shlex
import subprocess
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
TASK_REL = ".code-flow/tasks/2026-10-07/async-tool-runtime"
ARCHIVED_REL = ".code-flow/tasks/archived/2026-10-07/async-tool-runtime"
TASK_FILE_NAME = "async-tool-runtime.md"
MANIFEST_NAME = ".acceptance-manifest.json"
INVENTORY_REL = "tests/async_tool_runtime_inventory.py"

SCENARIO_ROW = re.compile(r"^[SEB]-\d+$")
TERMINAL = {"verified", "e2e_deferred"}
PLACEHOLDER = ("编码期填写", "待填", "TODO", "TBD")
PLACEHOLDER_STATUS = {"planned", "pending", "tbd", "todo", "待填"}
PATH_SUFFIXES = (".py", ".ts", ".json", ".ini")
KEY_TOKENS = ("-k", "-g")
EXPECTED_SCENARIOS = 43
EXPECTED_E2E = 29
EXPECTED_INTEGRATION = 14
DONE_STATUSES = {"done", "verified"}


def _dir() -> Path:
    """需求目录：未归档用 live 路径，归档后自动切到 archived（`/cf-task:archive` 会移动目录）。"""
    live = ROOT / TASK_REL
    archived = ROOT / ARCHIVED_REL
    assert live.exists() or archived.exists(), f"需求目录不存在：{TASK_REL} / {ARCHIVED_REL}"
    return live if live.exists() else archived


def _task_path() -> Path:
    return _dir() / TASK_FILE_NAME


def _manifest_path() -> Path:
    return _dir() / MANIFEST_NAME


def _read(path: Path) -> str:
    assert path.exists(), f"缺少文件：{path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _normalize_status(cell: str) -> str:
    """状态单元格取首个 token：E2E 行常写成 `e2e_deferred（终验归 verify-e2e）`。"""
    return re.split(r"[（(]", cell.strip())[0].strip()


def _section(block: str, heading: str) -> str:
    """取任务段落里某个 `### <heading>` 小节（到下一个 `###` 为止）。

    契约表与证据表都带同样的场景 ID，必须按小节取，否则「证据登记」检查会被契约表蒙混过关。
    """
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
    text = _read(_task_path())
    parts = re.split(r"^## (TASK-\d+): ", text, flags=re.MULTILINE)
    blocks: dict[str, str] = {}
    for index in range(1, len(parts), 2):
        blocks[parts[index]] = parts[index + 1]
    assert blocks, "未解析到任何 TASK 段落"
    return blocks


def _coverage_block() -> str:
    text = _read(_task_path())
    marker = "## Acceptance Coverage"
    assert marker in text, "缺少 Acceptance Coverage 段"
    tail = text[text.index(marker) + len(marker) :]
    return tail.split("\n## ")[0]


def _coverage_rows() -> list[dict[str, str]]:
    rows = _table(_coverage_block(), SCENARIO_ROW)
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
        }
        for cells in rows
    ]


def _coverage_row_line(scenario_id: str) -> str:
    for line in _coverage_block().splitlines():
        if line.startswith("|"):
            cells = _cells(line)
            if cells and cells[0] == scenario_id:
                return line
    raise AssertionError(f"覆盖表没有 {scenario_id} 行")


def _acceptance_refs(block: str) -> set[str]:
    match = re.search(r"^- \*\*Acceptance-Refs\*\*: (.+)$", block, re.MULTILINE)
    assert match, "缺少 Acceptance-Refs"
    return {item.strip() for item in match.group(1).split(",") if SCENARIO_ROW.match(item.strip())}


def _task_status(block: str) -> str:
    match = re.search(r"^- \*\*Status\*\*: (\S+)", block, re.MULTILINE)
    assert match, "缺少 Status"
    return match.group(1)


def _contract_rows(block: str) -> list[list[str]]:
    return _table(_section(block, "Acceptance Contract"), SCENARIO_ROW)


def _evidence_table_rows(block: str) -> dict[str, str]:
    return {
        cells[0]: _normalize_status(cells[-1])
        for cells in _table(_section(block, "Acceptance Evidence"), SCENARIO_ROW)
    }


def _evidence_entry_lines(block: str, scenario_id: str) -> list[str]:
    section = _section(block, "Acceptance Evidence")
    return [line for line in section.splitlines() if re.match(rf"^- {re.escape(scenario_id)}: ", line)]


def _evidence_entry_status(block: str, scenario_id: str) -> str | None:
    lines = _evidence_entry_lines(block, scenario_id)
    if not lines:
        return None
    match = re.match(rf"^- {re.escape(scenario_id)}: (\S+)", lines[-1])
    return match.group(1) if match else None


def _manifest_items() -> list[dict[str, Any]]:
    payload = json.loads(_read(_manifest_path()))
    assert isinstance(payload.get("scenarios"), list), "manifest 缺少 scenarios"
    return list(payload["scenarios"])


def _argv(cell: str) -> list[str] | None:
    """命令单元格：`-` / `planned` 等未登记记号返回 None，其余按 argv（JSON 数组或 shell words）解析。"""
    text = (cell or "").strip()
    if not text or text in {"-", "planned", "pending", "TBD"}:
        return None
    if text.startswith("["):
        parsed = json.loads(text)
        assert isinstance(parsed, list) and parsed, f"命令列不是非空数组：{cell}"
        return [str(item) for item in parsed]
    return shlex.split(text)


def _legs(command_cell: str) -> list[tuple[str, list[str]]]:
    """把登记命令拆成 `(相对 ROOT 的 cwd, argv)` 段。

    支持 JSON argv、`bash -lc "a && b"` 内层链与 `cd <dir>` 段——前端命令写作
    `cd e2e && npm test -- --config playwright.<domain>.config.ts`，路径必须相对 `e2e/` 解析。
    """
    text = command_cell.strip()
    argv = json.loads(text) if text.startswith("[") else shlex.split(text)
    if len(argv) >= 2 and argv[0] in ("bash", "sh") and argv[1] in ("-lc", "-c"):
        segments = [shlex.split(seg) for seg in " ".join(argv[2:]).split("&&") if seg.strip()]
    else:
        segments = [list(argv)]
    legs: list[tuple[str, list[str]]] = []
    cwd = "."
    for tokens in segments:
        if len(tokens) >= 2 and tokens[0] == "cd":
            cwd = posixpath.normpath(posixpath.join(cwd, tokens[1]))
            continue
        legs.append((cwd, tokens))
    return legs


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


def _resolve(cwd: str, path: str) -> Path:
    return ROOT / cwd / path


def _registered_commands() -> list[tuple[str, str]]:
    """每条登记命令：覆盖表各行 + 每个任务契约表各行 → (行 ID, 命令单元格)。"""
    commands = [(row["id"], row["command"]) for row in _coverage_rows()]
    for block in _task_blocks().values():
        commands += [(cells[0], cells[-2]) for cells in _contract_rows(block)]
    assert commands, "未解析到任何登记命令"
    return commands


def _check_coverage_rows() -> list[str]:
    rows = _coverage_rows()
    problems: list[str] = []
    seen: dict[str, int] = {}
    for row in rows:
        seen[row["id"]] = seen.get(row["id"], 0) + 1
    duplicated = sorted(sid for sid, count in seen.items() if count > 1)
    if duplicated:
        problems.append(f"覆盖表出现重复场景 ID：{duplicated}")
    unknown = sorted({row["owner"] for row in rows} - set(_task_blocks()))
    if unknown:
        problems.append(f"覆盖表负责人不是本需求的 TASK：{unknown}")
    if len(rows) != EXPECTED_SCENARIOS:
        problems.append(f"覆盖表场景行应为 {EXPECTED_SCENARIOS} 行，实为 {len(rows)}")
    e2e = sum(1 for row in rows if row["level"] == "E2E")
    integration = sum(1 for row in rows if row["level"] == "integration")
    if (e2e, integration) != (EXPECTED_E2E, EXPECTED_INTEGRATION):
        problems.append(
            f"覆盖表层级分布应为 E2E={EXPECTED_E2E}/integration={EXPECTED_INTEGRATION}，"
            f"实为 E2E={e2e}/integration={integration}"
        )
    for row in rows:
        if not row["level"] or not row["boundary"]:
            problems.append(f"{row['id']} 缺少层级或真实边界")
        if _argv(row["command"]) is None:
            problems.append(f"{row['id']} 缺少可执行命令")
    return problems


def _check_coverage_terminal() -> list[str]:
    return [
        f"{row['id']}（{row['owner']}）={row['status']}"
        for row in _coverage_rows()
        if row["status"] not in TERMINAL
    ]


def _check_manifest_matches_coverage() -> list[str]:
    coverage = {row["id"]: row for row in _coverage_rows()}
    manifest = {item["id"]: item for item in _manifest_items()}
    problems: list[str] = []
    if set(manifest) != set(coverage):
        extra = sorted(set(manifest) - set(coverage))
        missing = sorted(set(coverage) - set(manifest))
        problems.append(f"manifest 与覆盖表 ID 集合不一致：多 {extra}，缺 {missing}")
    for scenario_id in sorted(set(manifest) & set(coverage)):
        item, row = manifest[scenario_id], coverage[scenario_id]
        for label, got, want in (
            ("source", item.get("source"), row["source"]),
            ("level", item.get("level"), row["level"]),
            ("boundary", item.get("boundary"), row["boundary"]),
            ("owner", item.get("owner"), row["owner"]),
        ):
            if got != want:
                problems.append(f"{scenario_id} {label} 不一致：manifest={got!r} 覆盖表={want!r}")
        if item.get("command") != _argv(row["command"]):
            problems.append(
                f"{scenario_id} 命令不一致：manifest={item.get('command')} 覆盖表={_argv(row['command'])}"
            )
    return problems


def _check_owner_refs() -> list[str]:
    blocks = _task_blocks()
    by_owner: dict[str, set[str]] = {}
    for row in _coverage_rows():
        by_owner.setdefault(row["owner"], set()).add(row["id"])
    problems: list[str] = []
    for owner, scenario_ids in sorted(by_owner.items()):
        missing = sorted(scenario_ids - _acceptance_refs(blocks[owner]))
        if missing:
            problems.append(f"{owner} 的 Acceptance-Refs 漏登记：{missing}")
    return problems


def _check_evidence_registration() -> list[str]:
    """终态契约行在该 owner 的 Evidence 小节登记且状态一致（表格行优先，runner 条目次之）。"""
    blocks = _task_blocks()
    owner_of = {row["id"]: row["owner"] for row in _coverage_rows()}
    problems: list[str] = []
    for task_id, block in sorted(blocks.items()):
        for cells in _contract_rows(block):
            scenario_id, status = cells[0], _normalize_status(cells[-1])
            if status not in TERMINAL:
                continue
            owner = owner_of.get(scenario_id, task_id)
            evidence = blocks[owner]
            table = _evidence_table_rows(evidence)
            if scenario_id in table:
                if table[scenario_id] not in TERMINAL:
                    problems.append(f"{scenario_id} 在 {owner} 的 Evidence 表格状态为 {table[scenario_id]}")
                continue
            entry = _evidence_entry_status(evidence, scenario_id)
            if entry is None:
                problems.append(
                    f"{scenario_id}（owner={owner}，契约={status}）未出现在 {owner} 的 Evidence 小节"
                )
            elif entry not in TERMINAL:
                problems.append(f"{scenario_id} 在 {owner} 的 Evidence 条目状态为 {entry}")
    return problems


def _check_evidence_no_placeholder() -> list[str]:
    problems: list[str] = []
    for task_id, block in sorted(_task_blocks().items()):
        section = _section(block, "Acceptance Evidence")
        for line in section.splitlines():
            if line.startswith("|") and any(token in line for token in PLACEHOLDER):
                problems.append(f"{task_id}: {line.strip()[:60]}…")
        for scenario_id, status in _evidence_table_rows(block).items():
            if status in PLACEHOLDER_STATUS:
                problems.append(f"{task_id}:{scenario_id} 状态为占位值 {status}")
    return problems


def _check_contract_terminal() -> list[str]:
    problems: list[str] = []
    for task_id, block in sorted(_task_blocks().items()):
        for cells in _contract_rows(block):
            status = _normalize_status(cells[-1])
            if status not in TERMINAL:
                problems.append(f"{task_id}:{cells[0]}={status}")
    return problems


def _check_contract_refs() -> list[str]:
    """每个任务的契约表要为它 `Acceptance-Refs` 里的**每个** ID 都留一行（整行被删只有这条查得出）。"""
    problems: list[str] = []
    for task_id, block in sorted(_task_blocks().items()):
        rows = {cells[0] for cells in _contract_rows(block)}
        missing = sorted(_acceptance_refs(block) - rows)
        if missing:
            problems.append(f"{task_id} 契约表缺行：{missing}")
    return problems


def _check_commands_paths() -> list[str]:
    problems: list[str] = []
    checked = 0
    for row_id, command in _registered_commands():
        if _argv(command) is None:
            continue
        for cwd, leg in _legs(command):
            for path in _referenced_paths(leg):
                checked += 1
                if not _resolve(cwd, path).exists():
                    problems.append(f"{row_id} 命令引用的路径不存在：{path}（cwd={cwd}）")
    if checked < 60:
        problems.append(f"路径解析疑似失效，只检查到 {checked} 条")
    return problems


def _check_functional_rows_use_pytest_files() -> list[str]:
    problems: list[str] = []
    for row in _coverage_rows():
        if row["level"] != "integration":
            continue
        files = [
            path
            for _cwd, leg in _legs(row["command"])
            for path in _referenced_paths(leg)
            if path.endswith(".py")
        ]
        if not files:
            problems.append(f"{row['id']}（integration）命令未引用具体 pytest 文件")
    return problems


def _check_k_tokens() -> list[str]:
    """命令里的 `-k`/`-g` 令牌必须在同一 leg 引用的真实测试文件里命中真实用例名。"""
    problems: list[str] = []
    for row_id, command in _registered_commands():
        if _argv(command) is None:
            continue
        for cwd, leg in _legs(command):
            tokens = [leg[i + 1].strip("\"'") for i, raw in enumerate(leg[:-1]) if raw in KEY_TOKENS]
            if not tokens:
                continue
            files = [_resolve(cwd, path) for path in _referenced_paths(leg)]
            pytest_files = [path for path in files if path.suffix == ".py" and path.is_file()]
            if not pytest_files:
                problems.append(f"{row_id} 的 leg 带 `-k` 却没引用任何 pytest 文件：{' '.join(leg)}")
                continue
            source = " ".join(path.read_text(encoding="utf-8") for path in pytest_files)
            for token in tokens:
                if not re.search(rf"def test_\w*{re.escape(token)}\w*", source):
                    files_text = " ".join(str(path) for path in pytest_files)
                    problems.append(f"{row_id} 的 `-k {token}` 在 {files_text} 里没有对应用例")
    return problems


def _playwright_specs(config: Path) -> list[Path]:
    """Playwright 域配置经 `testDir` + `testMatch` 解析出的真实 spec 文件。"""
    text = config.read_text(encoding="utf-8")
    test_dir = re.search(r"testDir:\s*['\"]([^'\"]+)['\"]", text)
    base = config.parent / (test_dir.group(1) if test_dir else ".")
    match = re.search(r"testMatch:\s*\[([^\]]*)\]", text, re.S)
    patterns = re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)) if match else []
    specs: list[Path] = []
    for pattern in patterns:
        if any(char in pattern for char in "*?["):
            specs.extend(base.glob(pattern))
        else:
            specs.append(base / pattern)
    return [spec for spec in specs if spec.is_file()]


def _e2e_case_present(scenario_id: str, referenced: Path) -> bool:
    """场景 ID 在被引用文件里真实存在：pytest 看 `def test_*<id>*`，Playwright 看 test 标题。"""
    text = referenced.read_text(encoding="utf-8")
    if referenced.suffix == ".py":
        token = scenario_id.lower().replace("-", "")
        return bool(re.search(rf"def test_\w*{re.escape(token)}\w*", text))
    if referenced.suffix == ".ts":
        title = rf"test\(\s*['\"][^'\"]*{re.escape(scenario_id)}\b"
        return bool(re.search(title, text)) or any(
            re.search(title, spec.read_text(encoding="utf-8")) for spec in _playwright_specs(referenced)
        )
    return False


def _check_e2e_suites() -> list[str]:
    problems: list[str] = []
    for row in _coverage_rows():
        if row["level"] != "E2E":
            continue
        argv = _argv(row["command"])
        if argv is None:
            problems.append(f"{row['id']} 是 E2E 行却没有可执行命令")
            continue
        matched = any(
            _e2e_case_present(row["id"], resolved)
            for cwd, leg in _legs(row["command"])
            for path in _referenced_paths(leg)
            for resolved in [_resolve(cwd, path)]
            if resolved.is_file()
        )
        if not matched:
            problems.append(f"{row['id']} 未在被引用套件中找到同名真实用例")
    return problems


def _check_isolation() -> list[str]:
    """E2E 隔离机制在盘：独立空库 + 独立 Redis 号位 + 真实构建产物（QUALITY-04 / harness-test）。"""
    problems: list[str] = []
    conftest = ROOT / "tests/acceptance/conftest.py"
    if not conftest.is_file():
        problems.append("缺少验收隔离 conftest：tests/acceptance/conftest.py")
    else:
        text = conftest.read_text(encoding="utf-8")
        for token in ("autouse=True", "create_datastore", "MUAD_ACCEPTANCE_SHARED_DB", "REDIS_URL"):
            if token not in text:
                problems.append(f"验收 conftest 缺少隔离机制：{token}")
    datastores = ROOT / "tests/acceptance/datastores.py"
    if not datastores.is_file():
        problems.append("缺少 datastores 实现：tests/acceptance/datastores.py")
    else:
        text = datastores.read_text(encoding="utf-8")
        for token in ("REDIS_DB_START = 10", "SET NX EX"):
            if token not in text:
                problems.append(f"Redis 号位占位机制缺失：{token}")
    for path in ("tests/acceptance/test_datastores_redis_slots.py", "e2e/support/isolated-datastores.ts"):
        if not (ROOT / path).is_file():
            problems.append(f"缺少隔离机检/实现：{path}")
    config = ROOT / "e2e/playwright.run-observability.config.ts"
    if not config.is_file():
        problems.append("缺少 run-observability 域配置")
    else:
        text = config.read_text(encoding="utf-8")
        required_tokens = ("useIsolatedDatastores", "timezoneId: 'Asia/Shanghai'", "preview",
                           "reuseExistingServer: false")
        for token in required_tokens:
            if token not in text:
                problems.append(f"run-observability 域配置缺少：{token}")
        if "run dev" in text:
            problems.append("run-observability 域配置不得用 dev server（preview 真实构建产物）")
    makefile = ROOT / "Makefile"
    if not makefile.is_file() or "acceptance-e2e" not in makefile.read_text(encoding="utf-8"):
        problems.append("Makefile 缺少 acceptance-e2e 入口")
    elif "run build" not in makefile.read_text(encoding="utf-8"):
        problems.append("Makefile 的 acceptance-e2e 必须先 build 前端再跑浏览器套件")
    return problems


def _check_done_checklists() -> list[str]:
    """非 draft 任务段落内不得残留未勾选项（收口任务的 checklist 也必须全勾）。"""
    problems: list[str] = []
    for task_id, block in sorted(_task_blocks().items()):
        status = _task_status(block)
        if status in {"draft"}:
            continue
        unchecked = [line.strip() for line in block.splitlines() if line.strip().startswith("- [ ]")]
        if unchecked:
            problems.append(f"{task_id}（{status}）仍有 {len(unchecked)} 项未勾：{unchecked[0][:60]}…")
    return problems


def test_inventory_registers_its_own_command_path() -> None:
    """清单自身的命令路径在盘且登记在任务文档与 manifest 里——结构性 RED 的判定基准。"""
    assert (ROOT / INVENTORY_REL).is_file(), f"清单自身命令路径不在盘：{INVENTORY_REL}"
    assert INVENTORY_REL in _read(_task_path()), f"任务文档未登记清单命令路径：{INVENTORY_REL}"
    manifest_commands = [" ".join(item.get("command") or []) for item in _manifest_items()]
    assert any(INVENTORY_REL in command for command in manifest_commands), "manifest 未登记清单命令"


def test_missing_inventory_path_fails_registered_command(tmp_path: Path) -> None:
    """清单文件缺失时登记的 argv 必须失败（`file or directory not found`，exit=4）。

    在空目录里以同一解释器执行同一相对路径——这正是「先写用例后建清单」结构性 RED 的判定。
    """
    (tmp_path / "tests").mkdir()
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", INVENTORY_REL],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=180,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 4, (
        f"缺清单文件时登记 argv 应失败（exit=4），实为 {completed.returncode}：{output[-500:]}"
    )
    assert "not found" in output, output[-500:]


def test_coverage_rows_have_exactly_one_owner() -> None:
    problems = _check_coverage_rows()
    assert not problems, f"覆盖表结构缺口：{problems}"


def test_coverage_rows_are_terminal() -> None:
    problems = _check_coverage_terminal()
    assert not problems, f"覆盖表行未终态：{problems}"


def test_manifest_matches_coverage_table() -> None:
    problems = _check_manifest_matches_coverage()
    assert not problems, "manifest 与覆盖表不一致：\n" + "\n".join(problems)


def test_owner_acceptance_refs_cover_its_scenarios() -> None:
    problems = _check_owner_refs()
    assert not problems, f"Acceptance-Refs 覆盖缺口：{problems}"


def test_terminal_rows_are_registered_in_owner_evidence() -> None:
    problems = _check_evidence_registration()
    assert not problems, f"Evidence 登记缺口：{problems}"


def test_evidence_sections_have_no_placeholder_rows() -> None:
    problems = _check_evidence_no_placeholder()
    assert not problems, f"证据表残留占位行：{problems}"


def test_contract_rows_are_terminal() -> None:
    problems = _check_contract_terminal()
    assert not problems, f"契约行未终态：{problems}"


def test_contract_tables_cover_every_acceptance_ref() -> None:
    problems = _check_contract_refs()
    assert not problems, f"契约表覆盖缺口：{problems}"


def test_registered_commands_reference_paths_on_disk() -> None:
    problems = _check_commands_paths()
    assert not problems, f"登记命令路径缺口：{problems}"


def test_functional_rows_use_pytest_files() -> None:
    problems = _check_functional_rows_use_pytest_files()
    assert not problems, f"integration 行未引用具体 pytest 文件：{problems}"


def test_registered_commands_k_tokens_hit_real_cases() -> None:
    problems = _check_k_tokens()
    assert not problems, f"命令令牌未命中真实用例：{problems}"


def test_e2e_rows_point_to_real_suites_and_case_names() -> None:
    problems = _check_e2e_suites()
    assert not problems, f"E2E 场景未落在真实套件：{problems}"


def test_e2e_isolation_mechanisms_are_in_place() -> None:
    problems = _check_isolation()
    assert not problems, f"验收隔离机制缺口：{problems}"


def test_done_tasks_have_no_unchecked_items() -> None:
    problems = _check_done_checklists()
    assert not problems, f"任务存在未勾项：{problems}"


@contextmanager
def _perturbed(path: Path, replacements: Sequence[tuple[str, str]]) -> Iterator[None]:
    """字节备份 → 扰动 →（无论断言成败）逐字节还原；还原后再断言字节一致。"""
    original = path.read_bytes()
    text = original.decode("utf-8")
    for old, new in replacements:
        count = text.count(old)
        assert count == 1, f"扰动锚点不唯一（{path.name}）：{old[:80]!r} 出现 {count} 次"
        text = text.replace(old, new)
    try:
        path.write_bytes(text.encode("utf-8"))
        yield
    finally:
        path.write_bytes(original)
    assert path.read_bytes() == original, f"扰动未逐字节还原：{path.relative_to(ROOT)}"


@pytest.fixture(autouse=True)
def _surface_guard() -> Iterator[None]:
    """每条用例前后比对任务文件与 manifest 的字节：扰动必须逐字节还原，绝不外泄。"""
    paths = (_task_path(), _manifest_path())
    before = {path: path.read_bytes() for path in paths}
    yield
    for path, original in before.items():
        assert path.read_bytes() == original, f"用例留下了未还原的扰动：{path.relative_to(ROOT)}"


def test_perturbation_changed_status_turns_terminal_check_red() -> None:
    """扰动 (a) 改状态：覆盖表 E-06 的**当前终态**改 `planned` ⇒ 终态检查变红并指名条目，还原后复绿。

    不假设行一定是 `e2e_deferred`（全量终验后所有行都是 `verified`）；只要求当前是终态之一。
    """
    line = _coverage_row_line("E-06")
    cells = _cells(line)
    status = _normalize_status(cells[5])
    assert status in TERMINAL, line
    perturbed = line.replace(f"| {cells[5]} |", "| planned |", 1)
    with _perturbed(_task_path(), [(line + "\n", perturbed + "\n")]):
        problems = _check_coverage_terminal()
        assert any("E-06" in problem and "planned" in problem for problem in problems), problems
    assert _check_coverage_terminal() == []


def test_perturbation_removed_evidence_row_turns_registration_check_red() -> None:
    """扰动 (b) 删证据行：删掉 TASK-003 的 B-01 全部证据登记 ⇒ 登记检查变红并指名条目。"""
    lines = _evidence_entry_lines(_task_blocks()["TASK-003"], "B-01")
    assert lines, "TASK-003 的 B-01 证据登记形态发生变化"
    with _perturbed(_task_path(), [(line + "\n", "") for line in lines]):
        problems = _check_evidence_registration()
        assert any("B-01" in problem and "TASK-003" in problem for problem in problems), problems
    assert _check_evidence_registration() == []


def test_perturbation_forged_case_name_turns_k_token_check_red() -> None:
    """扰动 (c) 伪造用例名：B-04 命令加 `-k test_b09_forged_case_name` ⇒ 令牌检查变红并指名条目。"""
    line = _coverage_row_line("B-04")
    needle = '"tests/agent_runtime/test_tool_wait_state_machine.py"]'
    assert needle in line, line
    forged = line.replace(
        needle,
        '"tests/agent_runtime/test_tool_wait_state_machine.py", "-k", "test_b09_forged_case_name"]',
    )
    with _perturbed(_task_path(), [(line + "\n", forged + "\n")]):
        problems = _check_k_tokens()
        assert any("B-04" in problem and "test_b09_forged_case_name" in problem for problem in problems), (
            problems
        )
    assert _check_k_tokens() == []


def test_perturbation_changed_manifest_boundary_turns_manifest_check_red() -> None:
    """扰动 (d) 改 manifest 边界：B-04 的 boundary 加后缀 ⇒ manifest 比对变红并指名条目。"""
    needle = "PG wait_generation、epoch、canonical seq"
    with _perturbed(_manifest_path(), [(needle, needle + " FORGED")]):
        problems = _check_manifest_matches_coverage()
        assert any("B-04" in problem and "boundary" in problem for problem in problems), problems
    assert _check_manifest_matches_coverage() == []
