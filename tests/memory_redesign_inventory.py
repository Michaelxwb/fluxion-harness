"""TASK-031 需求 inventory：设计/任务/锁定 manifest/required Rule/真实测试盘面闭包 + 扰动取证。

不是静态快照：闭包检查读**真实**设计、任务文件、锁定 manifest、Spec 元数据与真实测试盘面，
裁决全部走仓库自身的真实解析器（`cf_acceptance_manifest` / `cf_spec_gate` / `cf_spec_metadata`）；
扰动用例对真实文本做最小改写后复用同一组检查函数，证明每条断言真的会红，而不是只做结构快照。
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".code-flow/scripts"))

from cf_acceptance_manifest import extract_manifest, validate_manifest  # noqa: E402
from cf_acceptance_schema import load_manifest, verified_evidence  # noqa: E402
from cf_spec_context import load_context  # noqa: E402
from cf_spec_gate import _task_sections, validate_plan_coverage  # noqa: E402
from cf_spec_metadata import load_spec_metadata  # noqa: E402

TASK_DIR = ROOT / ".code-flow/tasks/2026-10-12/memory-redesign"
TASK_FILE = TASK_DIR / "memory-redesign.md"
DESIGN_FILE = TASK_DIR / "memory-redesign.design.md"
MANIFEST_FILE = TASK_DIR / ".acceptance-manifest.json"
CONTEXT_FILE = TASK_DIR / "spec-context.yml"
MEMORY_SPEC = ROOT / ".code-flow/specs/memory/harness-memory.md"

RULE_REF = re.compile(r"^[a-z0-9-]+#RULE-[a-z0-9-]+$")
DESIGN_RULE = re.compile(r"^RULE-\d{2}$")
SCENARIO = re.compile(r"^[BES]-\d{2}$")
SCENARIO_MENTION = re.compile(r"[BES]-\d{2}")
PYTEST_TARGET = re.compile(r"^tests/[\w/.-]+\.py$")
DOMAIN_ARG = re.compile(r"^DOMAIN=([a-z0-9-]+)$")

pytestmark = pytest.mark.contract


def _section(text: str, start: str, stop: str) -> str:
    match = re.search(rf"(?ms)^{re.escape(start)}[^\n]*\n(.*?)(?=^{re.escape(stop)}|\Z)", text)
    return match.group(1) if match else ""


def _rows(section: str, first_cell: re.Pattern[str]) -> list[list[str]]:
    """表格数据行：首格匹配给定模式（表头/分隔行自然被过滤）。"""
    rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells and first_cell.fullmatch(cells[0]):
            rows.append(cells)
    return rows


def _argv(cells: list[str]) -> list[str] | None:
    for cell in cells:
        if not cell.startswith("["):
            continue
        try:
            value = json.loads(cell)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
            return value
    return None


def _pytest_targets(argv: list[str]) -> list[str]:
    """argv 里真实 pytest 目标文件；兼容 `bash -lc "… tests/x.py …"` 的 shell 字符串。"""
    targets: list[str] = []
    for arg in argv:
        for token in arg.split():
            if PYTEST_TARGET.fullmatch(token):
                targets.append(token)
    return targets


def _domains(argv: list[str]) -> list[str]:
    return [match.group(1) for arg in argv for match in (DOMAIN_ARG.fullmatch(arg),) if match]


def _design_rules(design_text: str) -> set[str]:
    return {cells[0] for cells in _rows(_section(design_text, "#### 2.5.1", "#### 2.5.2"), DESIGN_RULE)}


def _design_scenarios(design_text: str) -> set[str]:
    return {cells[0] for cells in _rows(_section(design_text, "#### 2.5.2", "#### 2.5.3"), SCENARIO)}


def _required_rules(context) -> set[str]:
    return {
        f"{binding.spec_id}#{rule.ref}"
        for binding in context.bindings
        for rule in binding.rules
        if rule.enforcement == "required"
    }


def collect_issues(
    task_path: Path,
    design_path: Path,
    manifest_path: Path,
    context_path: Path,
) -> list[str]:
    """真实闭包检查；返回空列表表示设计/任务/manifest/规则/测试盘面互相闭合。"""
    issues: list[str] = []
    task_text = task_path.read_text(encoding="utf-8")
    design_text = design_path.read_text(encoding="utf-8")
    context = load_context(str(context_path))
    design_rules = _design_rules(design_text)
    design_scenarios = _design_scenarios(design_text)
    if len(design_rules) != 15:
        issues.append(f"design_rule_count:{len(design_rules)}")
    if len(design_scenarios) != 31:
        issues.append(f"design_scenario_count:{len(design_scenarios)}")

    coverage = extract_manifest(str(task_path))
    coverage_ids = {item["id"] for item in coverage["scenarios"]}
    issues.extend(f"scenario_missing:{item}" for item in sorted(design_scenarios - coverage_ids))
    issues.extend(f"scenario_unknown:{item}" for item in sorted(coverage_ids - design_scenarios))

    valid, reason = validate_manifest(str(task_path), str(manifest_path))
    if not valid:
        issues.append(f"manifest_invalid:{reason}")
    plan = validate_plan_coverage(context, str(task_path))
    issues.extend(f"plan_coverage_block:{error.message}" for error in plan.errors)

    sections = _task_sections(task_text)
    issues.extend(_responsibility_issues(task_text, context, sections))
    issues.extend(_design_responsibility_issues(task_text, design_rules, design_scenarios, sections))
    issues.extend(_coverage_owner_issues(coverage["scenarios"], sections))
    issues.extend(_command_issues(coverage["scenarios"]))
    if manifest_path.is_file():
        issues.extend(_manifest_issues(load_manifest(manifest_path), coverage_ids))
    return issues


def _responsibility_issues(task_text: str, context, sections) -> list[str]:
    """Spec Responsibility 表 ↔ Context required Rule：owner/verifier/测试盘面。"""
    issues: list[str] = []
    section = _section(task_text, "## Spec Responsibility", "## Design Rule Responsibility")
    declared = {cells[0]: cells for cells in _rows(section, RULE_REF)}
    required = _required_rules(context)
    issues.extend(f"required_rule_missing:{ref}" for ref in sorted(required - set(declared)))
    issues.extend(f"required_rule_unknown:{ref}" for ref in sorted(set(declared) - required))
    spec = load_spec_metadata(str(MEMORY_SPEC))
    metadata = {verifier.rule: verifier.config["argv"] for verifier in spec.verifiers}
    for ref, cells in declared.items():
        owner, argv = cells[1], _argv(cells)
        if owner not in sections:
            issues.append(f"required_rule_owner_missing:{ref}:{owner}")
        elif ref not in sections[owner]:
            issues.append(f"required_rule_owner_mention_missing:{ref}")
        if argv is None:
            issues.append(f"required_rule_verifier_missing:{ref}")
        else:
            for target in _pytest_targets(argv):
                if not (ROOT / target).is_file():
                    issues.append(f"required_rule_verifier_file_missing:{ref}:{target}")
        if ref.startswith("harness-memory#") and argv != metadata.get(ref.split("#", 1)[1]):
            issues.append(f"memory_verifier_argv_mismatch:{ref}")
    return issues


def _design_responsibility_issues(task_text, design_rules, design_scenarios, sections) -> list[str]:
    issues: list[str] = []
    section = _section(task_text, "## Design Rule Responsibility", "## Execution order")
    declared = {cells[0]: cells for cells in _rows(section, DESIGN_RULE)}
    issues.extend(f"design_rule_missing:{rule}" for rule in sorted(design_rules - set(declared)))
    issues.extend(f"design_rule_unknown:{rule}" for rule in sorted(set(declared) - design_rules))
    for rule, cells in declared.items():
        owner = cells[1]
        if owner not in sections:
            issues.append(f"design_rule_owner_missing:{rule}:{owner}")
        for scenario in SCENARIO_MENTION.findall(cells[2]):
            if scenario not in design_scenarios:
                issues.append(f"design_rule_scenario_missing:{rule}:{scenario}")
    return issues


def _coverage_owner_issues(scenarios, sections) -> list[str]:
    issues: list[str] = []
    for item in scenarios:
        owner = item["owner"]
        if owner not in sections:
            issues.append(f"coverage_owner_missing:{item['id']}:{owner}")
        elif item["id"] not in sections[owner]:
            issues.append(f"coverage_owner_mention_missing:{item['id']}")
    return issues


def _manifest_issues(manifest, coverage_ids) -> list[str]:
    issues: list[str] = []
    manifest_ids = {row["id"] for row in manifest["scenarios"]}
    issues.extend(f"manifest_scenario_missing:{item}" for item in sorted(coverage_ids - manifest_ids))
    for row in manifest["scenarios"]:
        status = row.get("status")
        if status == "verified" and not verified_evidence(row):
            issues.append(f"scenario_evidence_missing:{row['id']}")
        if status not in ("verified", "e2e_deferred"):
            issues.append(f"scenario_status_unexpected:{row['id']}:{status}")
        if status == "e2e_deferred" and (row.get("evidence") or {}).get("status") != "e2e_deferred":
            issues.append(f"scenario_evidence_missing:{row['id']}")
    return issues


def _command_issues(scenarios) -> list[str]:
    """Coverage 声明的命令必须指向真实测试盘面（文件/浏览器域配置）。"""
    issues: list[str] = []
    for row in scenarios:
        argv = row.get("command") or []
        for target in _pytest_targets(argv):
            if not (ROOT / target).is_file():
                issues.append(f"scenario_command_file_missing:{row['id']}:{target}")
        for domain in _domains(argv):
            if not (ROOT / f"e2e/playwright.{domain}.config.ts").is_file():
                issues.append(f"e2e_domain_config_missing:{row['id']}:{domain}")
    return issues


def test_inventory_closure_holds_for_real_requirement_artifacts():
    issues = collect_issues(TASK_FILE, DESIGN_FILE, MANIFEST_FILE, CONTEXT_FILE)
    assert issues == []


@dataclass(frozen=True)
class _Mutated:
    task: Path
    manifest: Path


def _copy_artifacts(
    tmp_path: Path,
    *,
    task_text: str | None = None,
    manifest_update: Callable[[dict], None] | None = None,
) -> _Mutated:
    """把真实任务/锁定 manifest 复制进 tmp；可选最小改写（设计文件保持真实）。"""
    task = tmp_path / "memory-redesign.md"
    task.write_text(task_text if task_text is not None else TASK_FILE.read_text(encoding="utf-8"))
    data = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    if manifest_update is not None:
        manifest_update(data)
    manifest = tmp_path / ".acceptance-manifest.json"
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return _Mutated(task, manifest)


def _issues(tmp_path: Path, *, task_text: str | None = None, manifest_update=None) -> list[str]:
    mutated = _copy_artifacts(tmp_path, task_text=task_text, manifest_update=manifest_update)
    return collect_issues(mutated.task, DESIGN_FILE, mutated.manifest, CONTEXT_FILE)


def _without_line(text: str, marker: str) -> str:
    remaining = [line for line in text.splitlines(keepends=True) if marker not in line]
    assert len(remaining) < len(text.splitlines(keepends=True)), f"未找到待删除行：{marker}"
    return "".join(remaining)


def _assert_issue(issues: list[str], prefix: str) -> None:
    assert any(issue.startswith(prefix) for issue in issues), f"期望 {prefix}，实际 {issues}"


def test_inventory_detects_missing_required_rule_row(tmp_path):
    """扰动①：删掉 Spec Responsibility 的 rel-001 行（TASK-024 新增）→ 必须红。"""
    text = _without_line(TASK_FILE.read_text(encoding="utf-8"), "harness-rel#RULE-rel-001 | TASK-024")
    _assert_issue(_issues(tmp_path, task_text=text), "required_rule_missing:harness-rel#RULE-rel-001")


def test_inventory_detects_deleted_scenario(tmp_path):
    """扰动②：从 Acceptance Coverage 删掉 B-18 → 必须红。"""
    text = _without_line(TASK_FILE.read_text(encoding="utf-8"), "| B-18 |")
    _assert_issue(_issues(tmp_path, task_text=text), "scenario_missing:B-18")


def test_inventory_detects_changed_owner(tmp_path):
    """扰动③：把 B-03 的 owner 改成不存在的 TASK-999 → 必须红。"""
    text = TASK_FILE.read_text(encoding="utf-8").replace(
        "| TASK-009 | verified |", "| TASK-999 | verified |", 1
    )
    issues = _issues(tmp_path, task_text=text)
    _assert_issue(issues, "coverage_owner_missing:B-03:TASK-999")
    _assert_issue(issues, "manifest_invalid:")


def test_inventory_detects_changed_level(tmp_path):
    """扰动④：把 B-01 的层级从 unit 改成 manual → 锁定 manifest 漂移必须红。"""
    text = TASK_FILE.read_text(encoding="utf-8").replace(
        "| unit | unit，纯证据校验 |", "| manual | unit，纯证据校验 |", 1
    )
    _assert_issue(_issues(tmp_path, task_text=text), "manifest_invalid:")


def test_inventory_detects_changed_command(tmp_path):
    """扰动④'：把 B-02 的 argv 指向另一个既有测试文件 → 锁定 manifest 漂移必须红。"""
    text = TASK_FILE.read_text(encoding="utf-8").replace(
        '["uv","run","pytest","-q","tests/agent_core/test_memory_redesign_selection.py"]',
        '["uv","run","pytest","-q","tests/agent_core/test_memory_redesign_render.py"]',
        1,
    )
    _assert_issue(_issues(tmp_path, task_text=text), "manifest_invalid:")


def test_inventory_detects_fake_test_name(tmp_path):
    """扰动⑤：命令指向不存在的测试文件 → 必须红。"""
    text = TASK_FILE.read_text(encoding="utf-8").replace(
        "tests/agent_core/test_memory_redesign_evidence.py",
        "tests/agent_core/test_memory_redesign_not_real.py",
        1,
    )
    _assert_issue(_issues(tmp_path, task_text=text), "scenario_command_file_missing:B-01")


def test_inventory_detects_missing_evidence(tmp_path):
    """扰动⑥：把已 verified 的 B-05 证据清空 → 必须红。"""

    def strip_evidence(data: dict) -> None:
        for row in data["scenarios"]:
            if row.get("id") == "B-05":
                row.pop("evidence", None)

    _assert_issue(_issues(tmp_path, manifest_update=strip_evidence), "scenario_evidence_missing:B-05")


def test_inventory_detects_missing_design_rule(tmp_path):
    """扰动⑦：删掉设计规则责任表的 RULE-15 行 → 必须红。"""
    text = _without_line(TASK_FILE.read_text(encoding="utf-8"), "| RULE-15 |")
    _assert_issue(_issues(tmp_path, task_text=text), "design_rule_missing:RULE-15")
