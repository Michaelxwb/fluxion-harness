"""[收口] 16-agent-memory 需求闭合清单（TASK-005 承接面）。

以**真实盘面**为输入做闭合核对，而不是自查断言：任务文件的覆盖表/契约表/证据表、
`.acceptance-manifest.json`、每条命令引用的文件与 `-k` 令牌是否真的在盘。

闭合口径：
- 覆盖表每一行唯一负责人；manifest 与覆盖表同 ID、同层级、同真实边界、同 owner、同命令、同 cwd；
- **已 `done` 的任务不得残留未终态行** —— 这是「收口清单自己收窄判据」的拦阻点：
  未完成任务的行单独列出（不豁免，只是尚未到达终态），但已宣告 done 的任务行必须终态；
- 终态行必须在该 owner 的 Evidence 小节里有同 ID 的证据行，且证据表不得残留占位行；
- 非 manual 行 argv 引用的文件/目录在盘；E2E 行的 `-k` 令牌命中真实用例名；
- 需求目录 live→archived 双写（归档后本文件仍需可跑）。

需求级「全部行终态」的更强闭合断言在 `/cf-task:verify-e2e`（E2E 终验）时收敛。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TASK_REL = ".code-flow/tasks/2026-10-01/16-agent-memory"
ARCHIVED_REL = ".code-flow/tasks/archived/2026-10-01/16-agent-memory"
TASK_FILE_NAME = "16-agent-memory.md"

TERMINAL = {"verified", "e2e_deferred"}
DONE = {"done", "verified"}
COVERAGE_ROW = re.compile(r"^(?:[SEB]-\d+|RULE-[\w-]+)$")
PLACEHOLDERS = ("TBD", "编码期填写", "待填")


def _dir() -> Path:
    """需求目录：未归档用 live 路径，归档后自动切到 archived（`/cf-task:archive` 会移动目录）。"""
    live = ROOT / TASK_REL
    archived = ROOT / ARCHIVED_REL
    assert live.exists() or archived.exists(), f"需求目录不存在：{TASK_REL} / {ARCHIVED_REL}"
    return live if live.exists() else archived


def _text() -> str:
    return (_dir() / TASK_FILE_NAME).read_text(encoding="utf-8")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _task_blocks() -> dict[str, str]:
    blocks: dict[str, str] = {}
    for match in re.finditer(r"(?ms)^## (TASK-\d+):.*?(?=^## TASK-|\Z)", _text()):
        blocks[match.group(1)] = match.group(0)
    assert blocks, "任务文件里没有 TASK 段"
    return blocks


def _field(block: str, name: str) -> str:
    match = re.search(rf"(?m)^- \*\*{name}\*\*: (.*)$", block)
    return match.group(1).strip() if match else ""


def _coverage_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    in_table = False
    for line in _text().splitlines():
        if line.startswith("## Acceptance Coverage"):
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if not in_table or not line.startswith("| "):
            continue
        cells = _cells(line)
        if len(cells) == 10 and COVERAGE_ROW.match(cells[0]):
            rows.append(
                {
                    "id": cells[0],
                    "source": cells[1],
                    "level": cells[2],
                    "boundary": cells[3],
                    "owner": cells[4],
                    "status": cells[5],
                    "command": cells[6],
                    "cwd": cells[7],
                }
            )
    assert rows, "覆盖表为空"
    return rows


def _evidence_ids(block: str) -> set[str]:
    section = block.partition("### Acceptance Evidence")[2].partition("### Log")[0]
    ids: set[str] = set()
    for line in section.splitlines():
        if line.startswith("| ") and not line.startswith("|---"):
            cells = _cells(line)
            if cells and COVERAGE_ROW.match(cells[0]):
                ids.add(cells[0])
    return ids


def _manifest() -> dict[str, Any]:
    path = _dir() / ".acceptance-manifest.json"
    assert path.is_file(), "缺 .acceptance-manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_coverage_rows_have_unique_owner() -> None:
    rows = _coverage_rows()
    seen: dict[str, str] = {}
    for row in rows:
        assert row["owner"].startswith("TASK-"), f"{row['id']} 缺负责人"
        assert row["id"] not in seen, f"{row['id']} 重复登记"
        seen[row["id"]] = row["owner"]
    assert len(rows) == len(seen)


def test_done_tasks_have_no_pending_rows() -> None:
    """已 done 的任务不得残留未终态行（含 RULE 规则行）——拦「自查时把判据放宽」。"""
    blocks = _task_blocks()
    done = {task for task, block in blocks.items() if _field(block, "Status") in DONE}
    pending = [
        f"{row['id']}(owner={row['owner']}, status={row['status']})"
        for row in _coverage_rows()
        if row["owner"] in done and row["status"] not in TERMINAL
    ]
    assert not pending, f"已 done 任务存在未终态行：{', '.join(pending)}"


def test_terminal_rows_have_evidence_in_owner_task() -> None:
    blocks = _task_blocks()
    missing: list[str] = []
    for row in _coverage_rows():
        if row["status"] not in TERMINAL:
            continue
        block = blocks.get(row["owner"], "")
        if row["id"] not in _evidence_ids(block):
            missing.append(f"{row['id']}@{row['owner']}")
    assert not missing, f"终态行缺同 ID 证据：{', '.join(missing)}"


def test_evidence_tables_have_no_placeholder_rows() -> None:
    offenders: list[str] = []
    for task, block in _task_blocks().items():
        section = block.partition("### Acceptance Evidence")[2].partition("### Log")[0]
        for line in section.splitlines():
            if any(token in line for token in PLACEHOLDERS):
                offenders.append(f"{task}: {line.strip()[:60]}")
    assert not offenders, "证据表残留占位行：" + "; ".join(offenders)


def test_manifest_matches_coverage() -> None:
    manifest = {item["id"]: item for item in _manifest()["scenarios"]}
    rows = {row["id"]: row for row in _coverage_rows() if row["id"] in manifest}
    assert manifest.keys() == rows.keys(), "manifest 与覆盖表的场景集合不一致"
    for scenario_id, item in manifest.items():
        row = rows[scenario_id]
        assert item["owner"] == row["owner"], f"{scenario_id} owner 不一致"
        assert item["level"] == row["level"], f"{scenario_id} 层级不一致"
        assert item["boundary"] == row["boundary"], f"{scenario_id} 真实边界不一致"
        assert item["command"] == json.loads(row["command"]), f"{scenario_id} 命令不一致"
        assert item["cwd"] == row["cwd"], f"{scenario_id} cwd 不一致"


def test_commands_reference_existing_paths() -> None:
    missing: list[str] = []
    for row in _coverage_rows():
        try:
            argv = json.loads(row["command"])
        except json.JSONDecodeError:
            missing.append(f"{row['id']} 命令不是合法 argv")
            continue
        assert isinstance(argv, list)
        for token in argv:
            if token.startswith("tests/") or token.startswith("scripts/"):
                if not (ROOT / token).exists():
                    missing.append(f"{row['id']} → {token}")
    assert not missing, "命令引用的路径不在盘：" + "; ".join(missing)


def test_e2e_rows_register_k_tokens_that_hit_real_cases() -> None:
    """**场景行**的 E2E 登记必须带 `-k` 令牌且命中真实用例名（否则是空转登记）。

    RULE 行不适用：规则行的命令是 spec 自己的 verifier（如 RULE-test-001 是仓库级重链
    `tests/acceptance` + 前端 build + Playwright e2e），本就没有单用例令牌；它引用的路径在盘
    由 `test_commands_reference_existing_paths` 覆盖。
    """
    offending: list[str] = []
    for row in _coverage_rows():
        if row["level"] != "E2E" or not re.match(r"^[SEB]-\d+$", row["id"]):
            continue
        argv = json.loads(row["command"])
        if "-k" not in argv:
            offending.append(f"{row['id']} 缺 -k 令牌")
            continue
        token = argv[argv.index("-k") + 1]
        test_file = next((item for item in argv if item.startswith("tests/")), "")
        assert test_file, f"{row['id']} 未指向测试文件"
        source = (ROOT / test_file).read_text(encoding="utf-8")
        if not re.search(rf"def test_{re.escape(token)}_\w+", source):
            offending.append(f"{row['id']} 的 -k {token} 在 {test_file} 里没有对应用例")
    assert not offending, "E2E 登记与真实用例不符：" + "; ".join(offending)
