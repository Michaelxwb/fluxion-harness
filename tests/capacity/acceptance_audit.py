"""[B-318] 验收映射与规范回归证据收口（TASK-118；设计 §2.5 / §4.3 / §6）。

以**真实盘面**为输入做三方闭环核对，而不是对自查结果自查：

- **设计 → 覆盖表 → manifest**：设计 §2.5/§2.4 的每个场景在覆盖表里恰好一行、唯一负责人，
  负责人自己的 `Acceptance-Refs` 必须登记它；manifest 与覆盖表同 ID、同 owner、同层级、
  同真实边界、同 cwd/timeout/命令/状态；
- **证据不伪造**：verified 必须有真实命令、`exit_code=0`、`run_id`、`executed_at`，且 owner
  任务为 done；`e2e_deferred` 不得携带 passed/exit_code，owner 必须显式写出延后；
- **Spec 规则唯一责任**：每条 `harness-*#RULE-*` 只有一个 owner，owner 的 `Spec-Refs` 与契约表
  都有该行；收口任务自己的 `harness-test#RULE-test-001` 必须保留原 verifier 并 verified；
- **边界显式保留**：运行手册必须写出四层命令与未验证容量边界（真实供方额度、企微外部容量、
  探针吞吐、1000 用户全量、浏览器独立复核），不能用“全绿”吞掉未验证项。

设计 RULE/RISK 行不要求唯一 owner（一个规则可跨多个场景/多个交付任务），但要求该行声明的
owner 集合**恰好等于**其场景在覆盖表里的 owner 集合；唯一性在场景粒度上强制。

判定失败时消息带场景/规则 ID，便于直接定位任务文档那一行。运营口径见
`docs/operations/agent-capacity-webchat-validation.md`。
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TASK_DIR_REL = ".code-flow/tasks/2026-10-16/agent-capacity-webchat"
TASK_DIR_GLOB = ".code-flow/tasks/archived/*/agent-capacity-webchat"
TASK_FILE_NAME = "agent-capacity-webchat.md"
BACKEND_DESIGN_NAME = "agent-capacity-webchat.backend.design.md"
FRONTEND_DESIGN_NAME = "agent-capacity-webchat.frontend.design.md"
MANIFEST_NAME = ".acceptance-manifest.json"
VALIDATION_DOC_REL = "docs/operations/agent-capacity-webchat-validation.md"

AUDIT_TEST = "tests/capacity/test_capacity_webchat_acceptance_audit.py"
CLOSURE_TASK = "TASK-118"
CLOSURE_SCENARIO = "B-318"
CLOSURE_RULE_REF = "harness-test#RULE-test-001"
FUNCTIONAL_COMMAND = ("uv", "run", "pytest", "-q", AUDIT_TEST)
VERIFIER_COMMAND = (
    "bash",
    "-lc",
    "uv run pytest -q tests/acceptance && npm --prefix apps/console-platform/frontend run build "
    "&& npm --prefix e2e test",
)
VERIFIER_LAYERS = (
    "uv run pytest -q tests/acceptance",
    "npm --prefix apps/console-platform/frontend run build",
    "npm --prefix e2e test",
    "uv run pytest -q tests/capacity/test_capacity_webchat_acceptance_audit.py",
)
BOUNDARY_HEADING = "## 未验证边界（Unverified boundaries）"
BOUNDARY_TOPICS = ("真实供方", "企微", "探针", "全量", "浏览器")
MIN_BOUNDARY_ITEMS = 5

EXPECTED_DESIGN_SCENARIOS = 78
EXPECTED_SPEC_RULES = 27
EXPECTED_TRACE_RULES = 40
EXPECTED_DESIGN_RULES = 11
EXPECTED_RISKS = 29

TERMINAL = frozenset({"verified", "e2e_deferred"})
EVIDENCE_STATUSES = frozenset(
    {"verified", "e2e_deferred", "planned", "pending", "passed", "failed", "deferred", "review-deferred"}
)
DEFERRAL_MARKERS = re.compile(r"defer|verify-e2e|留", re.IGNORECASE)
PLACEHOLDERS = ("编码期填写", "待填", "TODO", "TBD")

SCENARIO_ID = re.compile(r"^[SEB]-\d+$")
DESIGN_SCENARIO_ID = re.compile(r"^[SEB]-F?\d{2}$")
ROW_ID = re.compile(r"^[SEB]-\d+$|^harness-[\w-]+#RULE-[\w-]+$|^RULE-[\w-]+$")
TRACE_ID = re.compile(r"^(RULE|RISK)-[\w-]+$")
TRACE_PREFIX = re.compile(r"^(RULE|RISK)-[\w-]+")
SPEC_RULE_REF = re.compile(r"^[\w-]+#RULE-[\w-]+$")
SCENARIO_TOKEN = re.compile(r"[SEB]-\d+")
TASK_HEADING = re.compile(r"^## (TASK-\d+): ", re.MULTILINE)
BULLET = re.compile(r"^- ([A-Z]+-\d+|RULE-[\w-]+|harness-[\w-]+#RULE-[\w-]+): (\S+)")
FAILURE_TEXT = re.compile(r"failed|error|exit\s*=\s*[12]|exit_code\"?\s*[:=]\s*[12]|exit=[12]", re.IGNORECASE)
PASS_TEXT = re.compile(r"passed|exit\s*=\s*0|exit_code\"?\s*[:=]\s*0|exit=0", re.IGNORECASE)


class AuditInputError(RuntimeError):
    """源文档缺失或结构不符合预期时显式失败（不回落默认值）。"""


@dataclass(frozen=True, slots=True)
class AuditFinding:
    """一条可定位的审计失败：code 稳定，scenario_id/rule_id 指到具体条目。"""

    code: str
    scenario_id: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.scenario_id}: {self.detail}"


@dataclass(frozen=True, slots=True)
class CoverageRow:
    scenario_id: str
    source: str
    level: str
    boundary: str
    owner: str
    status: str
    command: tuple[str, ...] | None
    cwd: str
    timeout: float


@dataclass(frozen=True, slots=True)
class Evidence:
    status: str
    command: tuple[str, ...] | None
    exit_code: int | None
    run_id: str | None
    executed_at: str | None


@dataclass(frozen=True, slots=True)
class ManifestRow:
    scenario_id: str
    owner: str
    level: str
    boundary: str
    status: str
    command: tuple[str, ...] | None
    cwd: str
    timeout: float
    evidence: Evidence | None
    runs: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class TaskRecord:
    task_id: str
    status: str
    acceptance_refs: frozenset[str]
    spec_refs: frozenset[str]
    contract: Mapping[str, str]
    evidence: Mapping[str, str]
    block: str


@dataclass(frozen=True, slots=True)
class TraceRule:
    rule_id: str
    scenarios: tuple[str, ...]
    owners: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SpecRule:
    rule_ref: str
    owner: str
    command: tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class AuditInputs:
    root: Path
    tasks: Mapping[str, TaskRecord]
    coverage: tuple[CoverageRow, ...]
    manifest: tuple[ManifestRow, ...]
    backend_scenarios: frozenset[str]
    frontend_scenarios: frozenset[str]
    design_scenarios: frozenset[str]
    frontend_map: Mapping[str, str]
    traceability: tuple[TraceRule, ...]
    spec_rules: tuple[SpecRule, ...]
    validation_doc: str


@dataclass(frozen=True, slots=True)
class AuditReport:
    findings: tuple[AuditFinding, ...]
    scenario_count: int
    design_scenario_count: int
    verified_count: int
    deferred_count: int
    spec_rule_count: int
    trace_rule_count: int

    @property
    def ok(self) -> bool:
        return not self.findings


def _task_dir(root: Path) -> Path:
    live = root / TASK_DIR_REL
    if live.is_dir():
        return live
    archived = sorted(root.glob(TASK_DIR_GLOB))
    if not archived:
        raise AuditInputError(f"需求目录不存在：{TASK_DIR_REL}")
    return archived[-1]


def _read(path: Path) -> str:
    if not path.is_file():
        raise AuditInputError(f"缺少文件：{path}")
    return path.read_text(encoding="utf-8")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _normalise_status(cell: str) -> str:
    return re.split(r"[（(]", cell.strip(), maxsplit=1)[0].strip().lower()


def _top_section(text: str, marker: str) -> str:
    if marker not in text:
        raise AuditInputError(f"缺少段落：{marker}")
    tail = text[text.index(marker) + len(marker) :]
    return tail.split("\n## ", maxsplit=1)[0]


def _sub_section(block: str, marker: str) -> str:
    if marker not in block:
        raise AuditInputError(f"缺少小节：{marker}")
    tail = block[block.index(marker) + len(marker) :]
    return tail.split("\n### ", maxsplit=1)[0]


def _table_rows(block: str) -> Iterable[list[str]]:
    for line in block.splitlines():
        if line.startswith("|"):
            cells = _cells(line)
            if cells:
                yield cells


def _parse_command(cell: str) -> tuple[str, ...] | None:
    text = (cell or "").strip()
    if not text or text in {"-", "planned", "pending", "TBD"}:
        return None
    if text.startswith("["):
        parsed: object = json.loads(text)
        if not isinstance(parsed, list) or not parsed or not all(isinstance(item, str) for item in parsed):
            raise AuditInputError(f"命令列不是非空字符串数组：{text}")
        return tuple(parsed)
    return tuple(shlex.split(text))


def _optional_command(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AuditInputError(f"manifest command 不是字符串数组：{value!r}")
    return tuple(value) if value else None


def _optional_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AuditInputError(f"{label} 不是整数：{value!r}")
    return value


def _optional_str(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AuditInputError(f"{label} 不是字符串：{value!r}")
    return value


def _required_str(item: Mapping[str, object], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str):
        raise AuditInputError(f"manifest 字段 {key} 不是字符串：{value!r}")
    return value


def _timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AuditInputError(f"timeout 不是数字：{value!r}")
    return float(value)


def _evidence(item: Mapping[str, object]) -> Evidence:
    return Evidence(
        status=_required_str(item, "status"),
        command=_optional_command(item.get("command")),
        exit_code=_optional_int(item.get("exit_code"), "evidence.exit_code"),
        run_id=_optional_str(item.get("run_id"), "evidence.run_id"),
        executed_at=_optional_str(item.get("executed_at"), "evidence.executed_at"),
    )


def _task_blocks(text: str) -> dict[str, str]:
    matches = list(TASK_HEADING.finditer(text))
    if not matches:
        raise AuditInputError("未解析到任何 TASK 段落")
    blocks: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks[match.group(1)] = text[match.end() : end]
    return blocks


def _id_set(line: str) -> frozenset[str]:
    return frozenset(item.strip() for item in line.split(",") if item.strip())


def _contract_rows(block: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for cells in _table_rows(_sub_section(block, "### Acceptance Contract")):
        if not ROW_ID.fullmatch(cells[0]):
            continue
        status_index = -1
        if len(cells) >= 8 and _normalise_status(cells[-2]) in EVIDENCE_STATUSES:
            status_index = -2
        rows[cells[0]] = _normalise_status(cells[status_index])
    return rows


def _evidence_rows(block: str) -> dict[str, str]:
    section = _sub_section(block, "### Acceptance Evidence")
    rows: dict[str, str] = {}
    for cells in _table_rows(section):
        if ROW_ID.fullmatch(cells[0]) and _normalise_status(cells[-1]) in EVIDENCE_STATUSES:
            rows[cells[0]] = _normalise_status(cells[-1])
    for line in section.splitlines():
        match = BULLET.match(line.strip())
        if match:
            rows.setdefault(match.group(1), _normalise_status(match.group(2)))
    return rows


def _task_record(task_id: str, block: str) -> TaskRecord:
    status_match = re.search(r"^- \*\*Status\*\*: (\S+)", block, re.MULTILINE)
    refs_match = re.search(r"^- \*\*Acceptance-Refs\*\*: (.*)$", block, re.MULTILINE)
    specs_match = re.search(r"^- \*\*Spec-Refs\*\*: (.*)$", block, re.MULTILINE)
    if not status_match:
        raise AuditInputError(f"{task_id} 缺少 Status")
    return TaskRecord(
        task_id=task_id,
        status=status_match.group(1).strip(),
        acceptance_refs=_id_set(refs_match.group(1) if refs_match else ""),
        spec_refs=_id_set(specs_match.group(1) if specs_match else ""),
        contract=_contract_rows(block),
        evidence=_evidence_rows(block),
        block=block,
    )


def _parse_coverage(text: str) -> tuple[CoverageRow, ...]:
    rows: list[CoverageRow] = []
    for cells in _table_rows(_top_section(text, "## Acceptance Coverage")):
        if not SCENARIO_ID.fullmatch(cells[0]):
            continue
        if len(cells) < 9:
            raise AuditInputError(f"覆盖表列数不足：{cells[0]} -> {len(cells)}")
        rows.append(
            CoverageRow(
                scenario_id=cells[0],
                source=cells[1],
                level=cells[2],
                boundary=cells[3],
                owner=cells[4],
                status=_normalise_status(cells[5]),
                command=_parse_command(cells[6]),
                cwd=cells[7],
                timeout=float(cells[8]),
            )
        )
    if not rows:
        raise AuditInputError("未解析到覆盖表")
    return tuple(rows)


def _parse_manifest(path: Path) -> tuple[ManifestRow, ...]:
    payload: object = json.loads(_read(path))
    if not isinstance(payload, Mapping):
        raise AuditInputError("manifest 顶层不是对象")
    raw = payload.get("scenarios")
    if not isinstance(raw, list):
        raise AuditInputError("manifest.scenarios 不是数组")
    rows: list[ManifestRow] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise AuditInputError("manifest 场景不是对象")
        evidence = item.get("evidence")
        runs = item.get("runs", [])
        if evidence is not None and not isinstance(evidence, Mapping):
            raise AuditInputError("manifest.evidence 不是对象")
        if not isinstance(runs, list) or not all(isinstance(run, Mapping) for run in runs):
            raise AuditInputError("manifest.runs 不是对象数组")
        rows.append(
            ManifestRow(
                scenario_id=_required_str(item, "id"),
                owner=_required_str(item, "owner"),
                level=_required_str(item, "level"),
                boundary=_required_str(item, "boundary"),
                status=_required_str(item, "status"),
                command=_optional_command(item.get("command")),
                cwd=_required_str(item, "cwd"),
                timeout=_timeout(item.get("timeout")),
                evidence=_evidence(evidence) if isinstance(evidence, Mapping) else None,
                runs=tuple(_evidence(run) for run in runs),
            )
        )
    return tuple(rows)


def _parse_design_scenarios(text: str, heading: str, pattern: re.Pattern[str]) -> frozenset[str]:
    ids = {
        cells[0]
        for cells in _table_rows(_top_section(text, heading))
        if cells and pattern.fullmatch(cells[0])
    }
    if not ids:
        raise AuditInputError(f"{heading} 未解析到设计场景")
    return frozenset(ids)


def _parse_frontend_map(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for cells in _table_rows(_top_section(text, "## Design Scenario Map")):
        if len(cells) >= 2 and SCENARIO_ID.fullmatch(cells[0]) and DESIGN_SCENARIO_ID.fullmatch(cells[1]):
            mapping[cells[0]] = cells[1]
    if not mapping:
        raise AuditInputError("未解析到 Design Scenario Map")
    return mapping


def _parse_traceability(text: str) -> tuple[TraceRule, ...]:
    rows: list[TraceRule] = []
    for cells in _table_rows(_top_section(text, "## Design Traceability")):
        if len(cells) < 4:
            continue
        match = TRACE_PREFIX.match(cells[1])
        if match is None:
            continue
        scenarios = tuple(SCENARIO_TOKEN.findall(cells[2]))
        owners = tuple(item.strip() for item in cells[3].split(",") if item.strip())
        rows.append(TraceRule(rule_id=match.group(0), scenarios=scenarios, owners=owners))
    if not rows:
        raise AuditInputError("未解析到 Design Traceability")
    return tuple(rows)


def _parse_spec_rules(text: str) -> tuple[SpecRule, ...]:
    rows: list[SpecRule] = []
    for cells in _table_rows(_top_section(text, "## Spec Responsibility")):
        if len(cells) < 4 or not SPEC_RULE_REF.fullmatch(cells[0]):
            continue
        rows.append(SpecRule(rule_ref=cells[0], owner=cells[1], command=_parse_command(cells[3])))
    if not rows:
        raise AuditInputError("未解析到 Spec Responsibility")
    return tuple(rows)


def load_audit_inputs(root: Path = ROOT) -> AuditInputs:
    """读取真实任务文档/设计/manifest/运行手册，构造只读审计盘面。"""
    directory = _task_dir(root)
    task_text = _read(directory / TASK_FILE_NAME)
    backend = _read(directory / BACKEND_DESIGN_NAME)
    frontend = _read(directory / FRONTEND_DESIGN_NAME)
    backend_scenarios = _parse_design_scenarios(backend, "### 2.5 验收条件", re.compile(r"^[SEB]-\d{2}$"))
    frontend_scenarios = _parse_design_scenarios(frontend, "### 2.4 验收条件", re.compile(r"^[SEB]-F\d{2}$"))
    frontend_map = _parse_frontend_map(task_text)
    design_scenarios = frozenset(backend_scenarios | frozenset(frontend_map))
    doc_path = root / VALIDATION_DOC_REL
    return AuditInputs(
        root=root,
        tasks={task_id: _task_record(task_id, block) for task_id, block in _task_blocks(task_text).items()},
        coverage=_parse_coverage(task_text),
        manifest=_parse_manifest(directory / MANIFEST_NAME),
        backend_scenarios=backend_scenarios,
        frontend_scenarios=frontend_scenarios,
        design_scenarios=design_scenarios,
        frontend_map=frontend_map,
        traceability=_parse_traceability(task_text),
        spec_rules=_parse_spec_rules(task_text),
        validation_doc=doc_path.read_text(encoding="utf-8") if doc_path.is_file() else "",
    )


def _finding(code: str, scenario_id: str, detail: str) -> AuditFinding:
    return AuditFinding(code=code, scenario_id=scenario_id, detail=detail)


def check_scenario_coverage(inputs: AuditInputs) -> list[AuditFinding]:
    """每个设计场景恰好一行、唯一 owner；owner 的 Acceptance-Refs 必须登记它。"""
    findings: list[AuditFinding] = []
    seen: dict[str, CoverageRow] = {}
    for row in inputs.coverage:
        if row.scenario_id in seen:
            findings.append(
                _finding("duplicate_scenario", row.scenario_id, f"覆盖表重复出现（owner={row.owner}）")
            )
        seen[row.scenario_id] = row
        task = inputs.tasks.get(row.owner)
        if task is None:
            findings.append(_finding("unknown_owner", row.scenario_id, f"负责人不存在：{row.owner}"))
        elif row.scenario_id not in task.acceptance_refs:
            findings.append(
                _finding("owner_ref_missing", row.scenario_id, f"{row.owner} 的 Acceptance-Refs 未登记")
            )
        if row.status not in TERMINAL:
            findings.append(_finding("non_terminal_scenario", row.scenario_id, f"状态={row.status}"))
        if not row.level or not row.boundary:
            findings.append(_finding("empty_boundary", row.scenario_id, "层级或真实边界为空"))
        if not row.source:
            findings.append(_finding("missing_source", row.scenario_id, "来源设计为空"))
        if row.command is None:
            findings.append(_finding("missing_command", row.scenario_id, "登记命令为空"))
    for scenario_id in sorted(inputs.design_scenarios - set(seen)):
        findings.append(_finding("design_scenario_missing", scenario_id, "设计场景未进入覆盖表"))
    return findings


def check_frontend_mapping(inputs: AuditInputs) -> list[AuditFinding]:
    """前端设计场景必须全部映射到覆盖表里的全局编号，且全局编号唯一。"""
    findings: list[AuditFinding] = []
    mapped_frontend = set(inputs.frontend_map.values())
    for frontend_id in sorted(inputs.frontend_scenarios - mapped_frontend):
        findings.append(_finding("frontend_mapping_missing", frontend_id, "Design Scenario Map 未映射"))
    coverage_ids = {row.scenario_id for row in inputs.coverage}
    for global_id, frontend_id in sorted(inputs.frontend_map.items()):
        if global_id not in coverage_ids:
            findings.append(
                _finding("frontend_mapping_missing", global_id, f"{frontend_id} 映射到不存在的场景")
            )
    duplicated = sorted(
        {
            value
            for value in inputs.frontend_map.values()
            if list(inputs.frontend_map.values()).count(value) > 1
        }
    )
    for frontend_id in duplicated:
        findings.append(_finding("frontend_mapping_duplicate", frontend_id, "多个全局场景映射到同一前端场景"))
    return findings


def check_manifest_alignment(inputs: AuditInputs) -> list[AuditFinding]:
    """manifest 与覆盖表同 ID/owner/层级/边界/cwd/timeout/命令/状态。"""
    findings: list[AuditFinding] = []
    coverage = {row.scenario_id: row for row in inputs.coverage}
    manifest: dict[str, ManifestRow] = {}
    for row in inputs.manifest:
        if row.scenario_id in manifest:
            findings.append(_finding("duplicate_manifest", row.scenario_id, "manifest 重复登记"))
        manifest[row.scenario_id] = row
    for scenario_id in sorted(set(coverage) - set(manifest)):
        findings.append(_finding("manifest_missing", scenario_id, "manifest 漏登记"))
    for scenario_id in sorted(set(manifest) - set(coverage)):
        findings.append(_finding("manifest_extra", scenario_id, "manifest 多出场景"))
    for scenario_id, row in sorted(manifest.items()):
        expected = coverage.get(scenario_id)
        if expected is None:
            continue
        fields = (
            ("owner", row.owner, expected.owner),
            ("level", row.level, expected.level),
            ("boundary", row.boundary, expected.boundary),
            ("cwd", row.cwd, expected.cwd),
            ("timeout", row.timeout, expected.timeout),
            ("command", row.command, expected.command),
            ("status", row.status, expected.status),
        )
        for name, actual, wanted in fields:
            if actual != wanted:
                findings.append(
                    _finding(
                        f"manifest_{name}_mismatch", scenario_id, f"manifest={actual!r} 覆盖表={wanted!r}"
                    )
                )
    return findings


def _check_verified(row: CoverageRow, item: ManifestRow, inputs: AuditInputs) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    evidence = item.evidence
    if evidence is None:
        return [_finding("verified_evidence_missing", row.scenario_id, "verified 但没有 evidence")]
    if evidence.status != "passed":
        findings.append(
            _finding("verified_evidence_not_passed", row.scenario_id, f"evidence={evidence.status}")
        )
    if evidence.exit_code != 0:
        findings.append(
            _finding("verified_evidence_incomplete", row.scenario_id, f"exit_code={evidence.exit_code}")
        )
    if evidence.command is None or evidence.command != row.command:
        findings.append(_finding("verified_evidence_incomplete", row.scenario_id, "命令缺失或与覆盖表不一致"))
    if not evidence.run_id or not evidence.executed_at:
        findings.append(_finding("verified_evidence_incomplete", row.scenario_id, "缺 run_id/executed_at"))
    if not item.runs:
        findings.append(_finding("verified_evidence_incomplete", row.scenario_id, "runs 为空"))
    task = inputs.tasks.get(row.owner)
    if task is None:
        return findings
    if task.status != "done" and row.owner != CLOSURE_TASK:
        findings.append(_finding("owner_not_done", row.scenario_id, f"{row.owner} 状态={task.status}"))
    status = task.evidence.get(row.scenario_id)
    if status is None:
        findings.append(
            _finding("owner_evidence_missing", row.scenario_id, f"{row.owner} 的 Evidence 未登记")
        )
    elif status in {"failed", "planned", "pending", "deferred"}:
        findings.append(_finding("owner_evidence_status_mismatch", row.scenario_id, f"evidence={status}"))
    return findings


def _check_deferred(row: CoverageRow, item: ManifestRow, inputs: AuditInputs) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    evidence = item.evidence
    if evidence is None:
        findings.append(
            _finding("deferred_evidence_missing", row.scenario_id, "e2e_deferred 但没有 evidence")
        )
    else:
        if evidence.status == "passed" or evidence.exit_code is not None:
            findings.append(
                _finding(
                    "deferred_evidence_claims_pass",
                    row.scenario_id,
                    f"status={evidence.status} exit_code={evidence.exit_code}",
                )
            )
        elif evidence.status != "e2e_deferred":
            findings.append(
                _finding("deferred_evidence_not_deferred", row.scenario_id, f"status={evidence.status}")
            )
        if evidence.command is not None and evidence.command != row.command:
            findings.append(
                _finding("evidence_command_mismatch", row.scenario_id, "evidence 命令与覆盖表不一致")
            )
    task = inputs.tasks.get(row.owner)
    if task is not None and not DEFERRAL_MARKERS.search(task.block):
        findings.append(_finding("deferral_not_disclosed", row.scenario_id, f"{row.owner} 未写明延后"))
    return findings


def _check_red_green(inputs: AuditInputs) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for task_id, task in sorted(inputs.tasks.items()):
        if "verified" not in task.contract.values():
            continue
        section = _sub_section(task.block, "### Acceptance Evidence")
        if "RED" not in section or "GREEN" not in section:
            findings.append(_finding("red_green_missing", task_id, "Evidence 缺 RED/GREEN 记录"))
        if not FAILURE_TEXT.search(section):
            findings.append(_finding("red_evidence_missing", task_id, "Evidence 缺失败/退出码非 0 记录"))
        if not PASS_TEXT.search(section):
            findings.append(_finding("green_evidence_missing", task_id, "Evidence 缺 passed/exit=0 记录"))
        for placeholder in PLACEHOLDERS:
            if placeholder in section:
                findings.append(_finding("placeholder_evidence", task_id, f"残留占位符：{placeholder}"))
    return findings


def check_evidence_integrity(inputs: AuditInputs) -> list[AuditFinding]:
    """verified 必须有真实回执；e2e_deferred 不得伪造 passed；RED/GREEN 不得缺失。"""
    findings: list[AuditFinding] = []
    by_id = {row.scenario_id: row for row in inputs.coverage}
    for item in inputs.manifest:
        row = by_id.get(item.scenario_id)
        if row is None:
            continue
        if row.status == "verified":
            findings.extend(_check_verified(row, item, inputs))
        elif row.status == "e2e_deferred":
            findings.extend(_check_deferred(row, item, inputs))
    findings.extend(_check_red_green(inputs))
    return findings


def check_traceability(inputs: AuditInputs) -> list[AuditFinding]:
    """设计 RULE/RISK 行的 owner 集合必须恰好等于其场景的 owner 集合。"""
    findings: list[AuditFinding] = []
    coverage = {row.scenario_id: row for row in inputs.coverage}
    seen: set[str] = set()
    rules = {row.rule_id for row in inputs.traceability if row.rule_id.startswith("RULE-")}
    risks = {row.rule_id for row in inputs.traceability if row.rule_id.startswith("RISK-")}
    if len(rules) != EXPECTED_DESIGN_RULES:
        findings.append(
            _finding("trace_rule_count", "RULE", f"RULE 行={len(rules)}，期望 {EXPECTED_DESIGN_RULES}")
        )
    if len(risks) != EXPECTED_RISKS:
        findings.append(_finding("trace_risk_count", "RISK", f"RISK 行={len(risks)}，期望 {EXPECTED_RISKS}"))
    for row in inputs.traceability:
        if row.rule_id in seen:
            findings.append(_finding("duplicate_trace_rule", row.rule_id, "追溯表重复出现"))
        seen.add(row.rule_id)
        if not row.scenarios:
            findings.append(_finding("trace_scenarios_missing", row.rule_id, "未绑定任何场景"))
            continue
        missing = [scenario for scenario in row.scenarios if scenario not in coverage]
        for scenario in missing:
            findings.append(_finding("trace_scenario_unknown", row.rule_id, f"场景不存在：{scenario}"))
        actual = tuple(sorted({coverage[s].owner for s in row.scenarios if s in coverage}))
        if actual != tuple(sorted(row.owners)):
            findings.append(
                _finding(
                    "traceability_owner_mismatch",
                    row.rule_id,
                    f"声明={row.owners} 实际={actual}",
                )
            )
        for owner in row.owners:
            if owner not in inputs.tasks:
                findings.append(_finding("trace_owner_unknown", row.rule_id, f"负责人不存在：{owner}"))
    return findings


def _check_closure_rule(inputs: AuditInputs) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    task = inputs.tasks.get(CLOSURE_TASK)
    if task is None:
        return [_finding("closure_task_missing", CLOSURE_TASK, "收口任务段落不存在")]
    if dict(task.contract).get(CLOSURE_RULE_REF) != "verified":
        findings.append(_finding("spec_rule_not_verified", CLOSURE_RULE_REF, "收口任务的 Rule 行未 verified"))
    if task.evidence.get(CLOSURE_RULE_REF) != "verified":
        findings.append(
            _finding("owner_evidence_missing", CLOSURE_RULE_REF, "收口任务 Evidence 未登记该 Rule")
        )
    joined = " ".join(VERIFIER_COMMAND)
    if joined not in task.block:
        findings.append(
            _finding("verifier_command_mismatch", CLOSURE_RULE_REF, "任务文档未保留原 verifier 命令")
        )
    scenario = next((row for row in inputs.coverage if row.scenario_id == CLOSURE_SCENARIO), None)
    if scenario is None:
        findings.append(_finding("closure_scenario_missing", CLOSURE_SCENARIO, "B-318 覆盖行不存在"))
    elif scenario.command != FUNCTIONAL_COMMAND:
        findings.append(_finding("closure_command_mismatch", CLOSURE_SCENARIO, f"{scenario.command}"))
    manifest = next((row for row in inputs.manifest if row.scenario_id == CLOSURE_SCENARIO), None)
    if manifest is None or manifest.command != FUNCTIONAL_COMMAND:
        findings.append(_finding("closure_command_mismatch", CLOSURE_SCENARIO, "manifest 命令不一致"))
    if not (inputs.root / AUDIT_TEST).is_file():
        findings.append(_finding("closure_audit_test_missing", CLOSURE_SCENARIO, AUDIT_TEST))
    return findings


def check_spec_responsibility(inputs: AuditInputs) -> list[AuditFinding]:
    """每条 Spec 规则恰好一个 owner；owner 的 Spec-Refs/契约表/Evidence 都有登记。"""
    findings: list[AuditFinding] = []
    seen: set[str] = set()
    for rule in inputs.spec_rules:
        if rule.rule_ref in seen:
            findings.append(_finding("duplicate_spec_rule", rule.rule_ref, "Spec Responsibility 重复登记"))
        seen.add(rule.rule_ref)
        task = inputs.tasks.get(rule.owner)
        if task is None:
            findings.append(
                _finding("spec_rule_owner_mismatch", rule.rule_ref, f"负责人不存在：{rule.owner}")
            )
            continue
        if rule.rule_ref not in task.spec_refs:
            findings.append(
                _finding("spec_rule_owner_mismatch", rule.rule_ref, f"{rule.owner} 的 Spec-Refs 未登记")
            )
        holders = [task_id for task_id, item in inputs.tasks.items() if rule.rule_ref in item.spec_refs]
        if holders != [rule.owner]:
            findings.append(
                _finding("spec_rule_owner_mismatch", rule.rule_ref, f"Spec-Refs 持有人={holders}")
            )
        if rule.command is None:
            findings.append(_finding("spec_rule_command_missing", rule.rule_ref, "verifier 命令为空"))
        if rule.rule_ref not in task.contract:
            findings.append(_finding("spec_rule_contract_missing", rule.rule_ref, f"{rule.owner} 契约表缺行"))
    if len(inputs.spec_rules) != EXPECTED_SPEC_RULES:
        findings.append(
            _finding(
                "spec_rule_count", "RULE", f"规则数={len(inputs.spec_rules)}，期望 {EXPECTED_SPEC_RULES}"
            )
        )
    closure = next((rule for rule in inputs.spec_rules if rule.rule_ref == CLOSURE_RULE_REF), None)
    if closure is None:
        findings.append(_finding("spec_rule_missing", CLOSURE_RULE_REF, "缺少收口规则"))
    elif closure.owner != CLOSURE_TASK or closure.command != VERIFIER_COMMAND:
        findings.append(
            _finding(
                "spec_rule_owner_mismatch",
                CLOSURE_RULE_REF,
                f"owner={closure.owner} command={closure.command}",
            )
        )
    findings.extend(_check_closure_rule(inputs))
    return findings


def _boundary_section(doc: str) -> str:
    if BOUNDARY_HEADING not in doc:
        return ""
    return doc[doc.index(BOUNDARY_HEADING) + len(BOUNDARY_HEADING) :].split("\n## ", maxsplit=1)[0]


def check_boundaries(inputs: AuditInputs) -> list[AuditFinding]:
    """运行手册必须写出四层命令，并显式保留未验证容量边界。"""
    findings: list[AuditFinding] = []
    doc = inputs.validation_doc
    if not doc.strip():
        return [_finding("runbook_missing", "RUNBOOK", VALIDATION_DOC_REL)]
    for layer in VERIFIER_LAYERS:
        if layer not in doc:
            findings.append(_finding("runbook_layer_missing", "RUNBOOK", layer))
    if CLOSURE_SCENARIO not in doc:
        findings.append(_finding("runbook_scenario_missing", "RUNBOOK", f"{CLOSURE_SCENARIO} 未登记"))
    section = _boundary_section(doc)
    if not section:
        findings.append(_finding("boundary_section_missing", "RUNBOOK", f"缺少 {BOUNDARY_HEADING}"))
        return findings
    items = [line for line in section.splitlines() if line.strip().startswith(("- ", "* "))]
    if len(items) < MIN_BOUNDARY_ITEMS:
        findings.append(
            _finding(
                "boundary_items_insufficient", "RUNBOOK", f"{len(items)} 项，至少 {MIN_BOUNDARY_ITEMS} 项"
            )
        )
    if "未验证" not in section:
        findings.append(_finding("boundary_not_honest", "RUNBOOK", "边界小节未出现“未验证”"))
    for topic in BOUNDARY_TOPICS:
        if topic not in section:
            findings.append(_finding("boundary_topic_missing", "RUNBOOK", topic))
    return findings


def audit_all(inputs: AuditInputs) -> AuditReport:
    """运行全部检查；返回稳定排序的 findings 与盘面计数。"""
    findings: list[AuditFinding] = []
    for check in (
        check_scenario_coverage,
        check_frontend_mapping,
        check_manifest_alignment,
        check_evidence_integrity,
        check_traceability,
        check_spec_responsibility,
        check_boundaries,
    ):
        findings.extend(check(inputs))
    findings.sort(key=lambda finding: (finding.code, finding.scenario_id, finding.detail))
    return AuditReport(
        findings=tuple(findings),
        scenario_count=len(inputs.coverage),
        design_scenario_count=len(inputs.design_scenarios),
        verified_count=sum(row.status == "verified" for row in inputs.coverage),
        deferred_count=sum(row.status == "e2e_deferred" for row in inputs.coverage),
        spec_rule_count=len(inputs.spec_rules),
        trace_rule_count=len(inputs.traceability),
    )
