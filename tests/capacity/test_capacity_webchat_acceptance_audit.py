"""[B-318] 验收映射与规范回归证据收口（TASK-118；设计 §2.5 / §4.3 / §6）。

本文件先于实现建立行为验收（RED），再驱动 `tests.capacity.acceptance_audit`：

- **真实盘面**：直接读取需求任务文档、`.acceptance-manifest.json`、两份设计文档与
  `docs/operations/agent-capacity-webchat-validation.md`，不以本文件里的自查常量代替；
- **必须有鉴别力**：对真实盘面做扰动（重复负责人、删设计场景、manifest 与证据状态打架、
  verified 缺命令/退出码、E2E deferred 伪造 passed、规则多负责人、未验证边界被删），
  每类扰动都必须变红并指名条目；逐项还原后真实盘面必须全绿；
- **不伪造**：verified 必须有命令、exit_code=0、run_id、executed_at 与 owner 的 done 状态；
  e2e_deferred 必须没有 exit_code、且 owner 的任务文档里明确写出延后（不能静默）；
- **诚实边界**：真实供方额度、企微外部容量、探针吞吐、1000 用户全量、浏览器独立复核
  等未验证项必须在运行手册里显式保留，不能被“全绿”吞掉。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import pytest

from tests.capacity.acceptance_audit import (
    AUDIT_TEST,
    CLOSURE_RULE_REF,
    CLOSURE_TASK,
    FUNCTIONAL_COMMAND,
    VERIFIER_COMMAND,
    VERIFIER_LAYERS,
    AuditInputs,
    Evidence,
    ManifestRow,
    SpecRule,
    audit_all,
    load_audit_inputs,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def inputs() -> AuditInputs:
    return load_audit_inputs()


def _codes(inputs: AuditInputs) -> set[str]:
    return {finding.code for finding in audit_all(inputs).findings}


def _verified_index(inputs: AuditInputs) -> int:
    return next(index for index, row in enumerate(inputs.manifest) if row.status == "verified")


def _deferred_index(inputs: AuditInputs) -> int:
    return next(index for index, row in enumerate(inputs.manifest) if row.status == "e2e_deferred")


def _replace_manifest(inputs: AuditInputs, index: int, row: ManifestRow) -> AuditInputs:
    rows = inputs.manifest[:index] + (row,) + inputs.manifest[index + 1 :]
    return replace(inputs, manifest=rows)


def _replace_coverage(inputs: AuditInputs, scenario_id: str, **changes: object) -> AuditInputs:
    rows = tuple(
        replace(row, **changes) if row.scenario_id == scenario_id else row  # type: ignore[arg-type]
        for row in inputs.coverage
    )
    return replace(inputs, coverage=rows)


def test_real_repository_acceptance_mapping_is_closed(inputs: AuditInputs) -> None:
    """真实盘面：190 条覆盖行、78 条设计场景、27 条 Spec 规则全部闭合且证据一致。"""
    report = audit_all(inputs)
    assert report.findings == (), "\n".join(str(finding) for finding in report.findings)
    assert report.scenario_count == 190, f"覆盖行应为 190，实为 {report.scenario_count}"
    assert report.design_scenario_count == 78, f"设计场景应为 78，实为 {report.design_scenario_count}"
    assert report.spec_rule_count == 27, f"Spec 规则应为 27，实为 {report.spec_rule_count}"
    assert report.trace_rule_count == 40, f"追溯规则/风险应为 40，实为 {report.trace_rule_count}"
    # 计数口径：功能层基线（≥128 行）必须 verified；其余只能是**显式** e2e_deferred，
    # 两者之和恰等于覆盖行数（任何非终态行都会在这里暴露）。不写死单一终态数字——
    # E2E 层被真实执行后 deferred 会转 verified，盘面计数随执行推进而变化，硬编码会把
    # 正常推进判成红。
    closure = next(row for row in inputs.coverage if row.scenario_id == "B-318")
    assert closure.status == "verified", f"收口行 B-318 必须 verified，实为 {closure.status}"
    assert report.verified_count >= 128, (
        f"verified 至少覆盖功能层基线 128 行，实为 {report.verified_count}"
    )
    assert report.verified_count + report.deferred_count == report.scenario_count, (
        f"覆盖行必须全部处于终态：verified={report.verified_count} + "
        f"deferred={report.deferred_count} != {report.scenario_count}"
    )


def test_duplicate_scenario_owner_is_rejected(inputs: AuditInputs) -> None:
    """同一场景出现两次（哪怕换了负责人）必须变红并指名场景。"""
    first = inputs.coverage[0]
    duplicated = replace(first, owner="TASK-999")
    perturbed = replace(inputs, coverage=inputs.coverage + (duplicated,))
    findings = audit_all(perturbed).findings
    matching = [f for f in findings if f.code == "duplicate_scenario" and f.scenario_id == first.scenario_id]
    assert matching, f"重复场景未被识别：{[str(f) for f in findings][:5]}"
    assert "TASK-999" in str(matching[0]) or first.scenario_id in str(matching[0])


def test_missing_design_scenario_is_rejected(inputs: AuditInputs) -> None:
    """设计文档里的场景从覆盖表整行消失（不是改成非终态）必须变红。"""
    target = "S-01"
    assert target in inputs.design_scenarios
    perturbed = replace(
        inputs,
        coverage=tuple(row for row in inputs.coverage if row.scenario_id != target),
    )
    findings = audit_all(perturbed).findings
    matching = [f for f in findings if f.code == "design_scenario_missing" and f.scenario_id == target]
    assert matching, f"缺失设计场景未被识别：{[str(f) for f in findings][:5]}"


def test_manifest_status_must_match_coverage_and_evidence(inputs: AuditInputs) -> None:
    """manifest 状态与覆盖表/证据状态三者不一致必须变红并指名场景。"""
    index = _verified_index(inputs)
    row = inputs.manifest[index]
    coverage_mismatch = _replace_manifest(inputs, index, replace(row, status="e2e_deferred"))
    findings = audit_all(coverage_mismatch).findings
    assert any(f.code == "manifest_status_mismatch" and f.scenario_id == row.scenario_id for f in findings), (
        f"manifest/覆盖表状态漂移未被识别：{[str(f) for f in findings][:5]}"
    )

    assert row.evidence is not None
    evidence_mismatch = _replace_manifest(
        inputs,
        index,
        replace(row, evidence=replace(row.evidence, status="e2e_deferred", exit_code=None)),
    )
    findings = audit_all(evidence_mismatch).findings
    assert any(
        f.code == "verified_evidence_not_passed" and f.scenario_id == row.scenario_id for f in findings
    ), f"verified 场景的 evidence 状态漂移未被识别：{[str(f) for f in findings][:5]}"


def test_verified_scenario_requires_recorded_command_exit_zero_and_run_id(inputs: AuditInputs) -> None:
    """宣称 verified 却没有命令/退出码/run_id 就是伪造成功回执，必须变红。"""
    index = _verified_index(inputs)
    row = inputs.manifest[index]
    assert row.evidence is not None
    stripped = replace(row.evidence, command=None, exit_code=None, run_id=None, executed_at=None)
    findings = audit_all(_replace_manifest(inputs, index, replace(row, evidence=stripped))).findings
    incomplete = [f for f in findings if f.code == "verified_evidence_incomplete"]
    assert incomplete, f"缺命令/退出码的 verified 未被识别：{[str(f) for f in findings][:5]}"
    assert any(f.scenario_id == row.scenario_id for f in incomplete)


def test_verified_scenario_requires_non_zero_conflicting_exit_code_to_fail(inputs: AuditInputs) -> None:
    """exit_code=1 的“passed”回执必须变红；不能把失败记成通过。"""
    index = _verified_index(inputs)
    row = inputs.manifest[index]
    assert row.evidence is not None
    failed = replace(row.evidence, exit_code=1)
    findings = audit_all(_replace_manifest(inputs, index, replace(row, evidence=failed))).findings
    assert any(
        f.code == "verified_evidence_incomplete" and f.scenario_id == row.scenario_id for f in findings
    ), f"exit_code=1 的 verified 未被识别：{[str(f) for f in findings][:5]}"


def test_e2e_deferred_scenario_cannot_carry_passed_evidence(inputs: AuditInputs) -> None:
    """E2E 延后行只能有延后回执；伪造 exit_code=0/passed 必须变红。"""
    index = _deferred_index(inputs)
    row = inputs.manifest[index]
    assert row.evidence is not None
    fabricated = replace(row.evidence, status="passed", exit_code=0)
    findings = audit_all(_replace_manifest(inputs, index, replace(row, evidence=fabricated))).findings
    assert any(
        f.code == "deferred_evidence_claims_pass" and f.scenario_id == row.scenario_id for f in findings
    ), f"E2E 延后行携带 passed 未被识别：{[str(f) for f in findings][:5]}"


def test_spec_rule_must_have_exactly_one_owner(inputs: AuditInputs) -> None:
    """Spec 规则被两个 TASK 同时认领（或负责人不在 Spec-Refs 里）必须变红。"""
    rule = inputs.spec_rules[0]
    other_owner = next(task_id for task_id in inputs.tasks if task_id != rule.owner)
    duplicated = inputs.spec_rules + (replace(rule, owner=other_owner),)
    findings = audit_all(replace(inputs, spec_rules=duplicated)).findings
    assert any(f.code == "duplicate_spec_rule" and f.scenario_id == rule.rule_ref for f in findings), (
        f"规则多负责人未被识别：{[str(f) for f in findings][:5]}"
    )

    wrong_owner = inputs.spec_rules[:1] + (replace(rule, owner=other_owner),) + inputs.spec_rules[1:]
    findings = audit_all(replace(inputs, spec_rules=wrong_owner)).findings
    assert any(f.code == "spec_rule_owner_mismatch" and f.scenario_id == rule.rule_ref for f in findings), (
        f"规则负责人不在 Spec-Refs 未被识别：{[str(f) for f in findings][:5]}"
    )


def test_design_traceability_owner_set_must_match_scenario_owners(inputs: AuditInputs) -> None:
    """设计 RULE/RISK 行声称的最终负责人必须等于其场景在覆盖表里的负责人集合。"""
    row = inputs.traceability[0]
    perturbed_rows = (replace(row, owners=("TASK-999",)),) + inputs.traceability[1:]
    findings = audit_all(replace(inputs, traceability=perturbed_rows)).findings
    assert any(f.code == "traceability_owner_mismatch" and f.scenario_id == row.rule_id for f in findings), (
        f"追溯负责人漂移未被识别：{[str(f) for f in findings][:5]}"
    )


def test_unverified_capacity_boundaries_must_be_retained(inputs: AuditInputs) -> None:
    """未验证容量边界被删掉/没有显式保留时必须变红（不能让全绿吞掉边界）。"""
    assert inputs.validation_doc.strip(), "运行手册缺失"
    perturbed = replace(inputs, validation_doc=inputs.validation_doc.split("## 未验证边界")[0])
    findings = audit_all(perturbed).findings
    assert any(f.code == "boundary_section_missing" for f in findings), (
        f"未验证边界小节缺失未被识别：{[str(f) for f in findings][:5]}"
    )

    stripped = replace(inputs, validation_doc=inputs.validation_doc.replace("真实供方", "真实模型"))
    findings = audit_all(stripped).findings
    assert any(f.code == "boundary_topic_missing" and "真实供方" in str(f) for f in findings), (
        f"边界主题被替换未被识别：{[str(f) for f in findings][:5]}"
    )


def test_runbook_documents_every_verifier_layer(inputs: AuditInputs) -> None:
    """运行手册必须逐条写出四层命令；删掉任意一层都要变红并指名。"""
    for layer in VERIFIER_LAYERS:
        assert layer in inputs.validation_doc, f"运行手册缺层：{layer}"
        findings = audit_all(
            replace(inputs, validation_doc=inputs.validation_doc.replace(layer, "<removed>"))
        ).findings
        assert any(f.code == "runbook_layer_missing" and layer in str(f) for f in findings), (
            f"缺层未被识别：{layer} / {[str(f) for f in findings][:5]}"
        )


def test_closure_rule_is_verified_with_the_exact_review_verifier(inputs: AuditInputs) -> None:
    """收口任务自己的 Rule 必须 verified，命令逐字等于 Spec 原 verifier（不得弱化）。"""
    task = inputs.tasks[CLOSURE_TASK]
    assert dict(task.contract).get(CLOSURE_RULE_REF) == "verified"
    assert CLOSURE_RULE_REF in task.spec_refs
    rule = next(rule for rule in inputs.spec_rules if rule.rule_ref == CLOSURE_RULE_REF)
    assert rule.owner == CLOSURE_TASK
    assert rule.command == VERIFIER_COMMAND
    assert "exit=0" in task.block or "exit 0" in task.block
    scenario = next(row for row in inputs.coverage if row.scenario_id == "B-318")
    assert scenario.command == FUNCTIONAL_COMMAND
    assert (inputs.root / AUDIT_TEST).is_file()


def test_finding_messages_name_the_offending_entry(inputs: AuditInputs) -> None:
    """失败消息必须带场景/规则 ID 或 TASK，便于直接定位任务文档那一行。"""
    target = "S-02"
    perturbed = _replace_coverage(inputs, target, owner="TASK-999")
    messages = [str(finding) for finding in audit_all(perturbed).findings]
    assert any(target in message for message in messages), f"失败消息未指名条目：{messages[:5]}"


def test_inputs_expose_immutable_typed_records(inputs: AuditInputs) -> None:
    """审计模型是只读强类型记录；扰动只能通过 replace 产生，不原地改真实盘面。"""
    row = inputs.manifest[0]
    assert isinstance(row, ManifestRow)
    assert isinstance(row.evidence, Evidence) or row.evidence is None
    assert isinstance(inputs.tasks, Mapping)
    assert isinstance(inputs.spec_rules[0], SpecRule)
    original = inputs.coverage[0]
    with pytest.raises(AttributeError):
        original.owner = "TASK-999"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        inputs.spec_rules[0].owner = "TASK-999"  # type: ignore[misc]
