"""[B-209] 概览需求收口清单：覆盖表/契约表/manifest/证据/规则归属的闭合核对。

判据（与 11-audit-observability 的收口清单同口径）：
- 覆盖表每行有且仅有一个负责人；除本收口任务自身外**全部终态**（`verified` / `e2e_deferred`）；
- manifest 与覆盖表的 id / 层级 / 负责人一致；
- 每个 owner 的 `Acceptance-Refs` 都在覆盖表登记；
- 终态场景在 owner 任务的 Acceptance Evidence 中登记；
- 每个任务的契约行全终态；`done` / `verified` 的任务零未勾 checklist；
- E2E 命令指向真实在盘套件；成功路径不得用路由拦截；全文不得出现跳过/期望失败标记；
- 6 条 required 规则各有唯一负责人且登记了可执行命令。

本清单在 TASK-011 收尾时执行；`-k b209` 为 manifest 登记 argv。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT / ".code-flow/tasks/2026-09-17/12-overview-dashboard"
TASK_FILE = TASK_DIR / "12-overview-dashboard.md"
MANIFEST = TASK_DIR / ".acceptance-manifest.json"

TERMINAL_STATUSES = {"verified", "e2e_deferred"}
# 本收口任务自身的行在 finish 前允许非终态
EXEMPT_OWNER = "TASK-011"
# 覆盖表里规则行用**短 ref** 作键（`RULE-xxx-00N`）
REQUIRED_RULES = (
    "RULE-api-001",
    "RULE-time-001",
    "RULE-data-001",
    "RULE-front-001",
    "RULE-ui-001",
    "RULE-ui-detail-001",
    "RULE-i18n-001",
    "RULE-test-001",
)
# 成功路径（E2E 场景）严禁出现路由拦截标记；其中 E-* 允许
# S-01 由后端验收套件（tests/acceptance/overview）承载，不在 Playwright spec 内
SUCCESS_E2E_SCENARIOS = {"S-02", "S-03", "S-04"}
SKIP_MARKERS = ("pytest.skip", "pytest.mark.skip", "skipif", "xfail", "@pytest.mark.skip")


def _text() -> str:
    return TASK_FILE.read_text(encoding="utf-8")


def _rows(section: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in section.splitlines():
        match = re.match(r"^\| ([A-Za-z0-9\-]+) \|", line)
        if not match or match.group(1) == "场景ID":
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        out.setdefault(match.group(1), []).append(cells)
    return out


def _section(body: str, name: str) -> str:
    match = re.search(rf"^### {name}$(.*?)(?=^### |^## |\Z)", body, re.S | re.M)
    return match.group(1) if match else ""


def _task_sections() -> dict[str, str]:
    text = _text()
    matches = list(re.finditer(r"(?m)^## (TASK-\d+):[^\n]*$", text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1)] = text[match.start() : end]
    return sections


def _coverage_rows() -> dict[str, list[str]]:
    text = _text()
    start = text.index("## Acceptance Coverage")
    end = text.index("## TASK-", start)
    return _rows(text[start:end])


def test_b209_coverage_has_single_owner_and_is_terminal() -> None:
    """覆盖表：每行唯一负责人；除本收口任务外全部终态。"""
    rows = _coverage_rows()
    assert len(rows) >= 17, f"覆盖表行数异常：{len(rows)}"
    for scenario, entries in rows.items():
        assert len(entries) == 1, f"{scenario} 在覆盖表出现多次"
        owner, status = entries[0][4], entries[0][5]
        assert owner.startswith("TASK-"), f"{scenario} 缺负责人：{owner!r}"
        if owner == EXEMPT_OWNER:
            continue
        assert status in TERMINAL_STATUSES, f"{scenario} 状态非终态：{status}（owner {owner}）"


def test_b209_manifest_matches_coverage() -> None:
    """manifest 与覆盖表的 id / 层级 / 负责人一致。"""
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    coverage = _coverage_rows()
    scenarios = {item["id"]: item for item in data["scenarios"]}
    assert set(scenarios) == {key for key in coverage if not key.startswith("RULE-")}, (
        "manifest 与覆盖表的场景集合不一致"
    )
    for scenario, item in scenarios.items():
        owner, level = coverage[scenario][0][4], coverage[scenario][0][2]
        assert item["owner"] == owner, f"{scenario} 负责人不一致：{item['owner']} vs {owner}"
        assert item["level"] == level, f"{scenario} 层级不一致：{item['level']} vs {level}"


def test_b209_owner_refs_are_registered_in_coverage() -> None:
    """每个 owner 的 Acceptance-Refs 都能在覆盖表找到登记。"""
    coverage = set(_coverage_rows())
    for task, body in _task_sections().items():
        refs = re.search(r"- \*\*Acceptance-Refs\*\*: (.*)", body)
        assert refs, f"{task} 缺 Acceptance-Refs"
        for ref in (item.strip() for item in refs.group(1).split(",")):
            if not ref:
                continue
            assert ref in coverage, f"{task} 的 ref {ref} 未在覆盖表登记"


def test_b209_terminal_scenarios_registered_in_owner_evidence() -> None:
    """终态场景必须在其 owner 任务的 Acceptance Evidence 中登记。"""
    rows = _coverage_rows()
    for scenario, entries in rows.items():
        if scenario.startswith("RULE-"):
            continue
        owner, status = entries[0][4], entries[0][5]
        if status not in TERMINAL_STATUSES:
            continue
        body = _task_sections()[owner]
        evidence = _section(body, "Acceptance Evidence")
        assert scenario in evidence, f"{scenario}（{status}）未在 {owner} 的证据中登记"


def test_b209_contract_rows_are_terminal() -> None:
    """每个任务的契约行全部终态（本收口任务自身的行在 finish 前允许非终态）。"""
    for task, body in _task_sections().items():
        if task == EXEMPT_OWNER:
            continue
        contract = _section(body, "Acceptance Contract")
        for scenario, entries in _rows(contract).items():
            status = entries[0][-1]
            assert status in TERMINAL_STATUSES, f"{task} 的契约行 {scenario} 非终态：{status}"


def test_b209_done_tasks_have_no_unchecked_items() -> None:
    """done / verified 的任务不得残留未勾 checklist。"""
    for task, body in _task_sections().items():
        status = re.search(r"- \*\*Status\*\*: (\S+)", body)
        assert status, f"{task} 缺 Status"
        if status.group(1) not in {"done", "verified"}:
            continue
        unchecked = re.findall(r"^- \[ \]", _section(body, "Checklist"), re.M)
        assert unchecked == [], f"{task} 已 {status.group(1)} 却有 {len(unchecked)} 项未勾"


def test_b209_required_rules_have_owner_and_command() -> None:
    """8 条 required 规则各有唯一负责人，且覆盖行登记了可执行命令。"""
    rows = _coverage_rows()
    for rule in REQUIRED_RULES:
        assert rule in rows, f"覆盖表缺规则行：{rule}"
        entry = rows[rule][0]
        assert entry[4].startswith("TASK-"), f"{rule} 缺负责人"
        argv = json.loads(entry[6])
        assert isinstance(argv, list) and argv, f"{rule} 的命令为空"


def test_b209_no_skip_markers_and_e2e_commands_point_to_real_suites() -> None:
    """全文无跳过/期望失败标记；E2E 命令指向真实在盘套件，成功路径不得用路由拦截。"""
    text = _text()
    for marker in SKIP_MARKERS:
        assert marker not in text, f"任务文档出现跳过标记：{marker}"

    spec = (ROOT / "e2e/tests/overview-dashboard.spec.ts").read_text(encoding="utf-8")
    for scenario in SUCCESS_E2E_SCENARIOS:
        match = re.search(rf"^test\('{scenario} [^']*'", spec, re.M)
        assert match, f"spec 缺 {scenario} 用例"
        block = spec[match.start() :]
        following = block.find("\ntest(", 1)
        block = block if following == -1 else block[:following]
        assert "page.route(" not in block, f"{scenario} 是成功路径，不得使用路由拦截"

    for scenario, entries in _coverage_rows().items():
        argv = json.loads(entries[0][6])
        joined = " ".join(argv)
        for token in re.findall(r"(tests/\S+\.py|e2e/\S+\.ts|playwright\.\S+\.ts)", joined):
            # 命令含 `cd e2e` 时，playwright.*.config.ts 的基准目录是 e2e/
            candidates = (ROOT / token, ROOT / "e2e" / token)
            if not any(path.exists() for path in candidates):
                raise AssertionError(f"{scenario} 的命令指向不存在的文件：{token}")
