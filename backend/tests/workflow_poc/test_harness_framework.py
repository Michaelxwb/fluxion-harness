"""PoC 断言框架自检：TASK-003/004/002 复用前先证明框架自身正确。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.workflow_poc.dbos_testing import _merge_existing_criteria as merge_dbos_evidence
from tests.workflow_poc.poc_workflow import (
    POC_WORKFLOW_STEPS,
    CriterionOutcome,
    MockRetentionGuard,
    PocCriteriaReport,
    PocCriterion,
    RetentionBlockedError,
    TraceCorrelator,
)
from tests.workflow_poc.restate_testing import (
    _merge_existing_criteria as merge_restate_evidence,
)


def test_poc_workflow_defines_five_unified_steps() -> None:
    """统一 PoC workflow：5 step 与 roadmap TASK-0002 口径一一对应。"""
    kinds = [step.kind for step in POC_WORKFLOW_STEPS]
    assert kinds == [
        "idempotent-write",
        "timer",
        "timeout",
        "external-approval-signal",
        "http-activity",
    ]
    assert len({step.name for step in POC_WORKFLOW_STEPS}) == 5


def test_criteria_report_aggregation() -> None:
    """口径聚合：all_passed 仅在 7 口径全 passed 时为 True。"""
    report = PocCriteriaReport()
    for criterion in PocCriterion:
        report.add(CriterionOutcome(criterion=criterion, passed=True, detail="ok"))
    assert report.all_passed()
    assert len(report.outcomes) == 7

    failing = PocCriteriaReport()
    failing.add(CriterionOutcome(criterion=PocCriterion.CRASH, passed=True, detail="ok"))
    failing.add(CriterionOutcome(criterion=PocCriterion.TIMER, passed=False, detail="restart 后 timer 丢失"))
    assert not failing.all_passed()
    assert failing.to_dict()["outcomes"][1]["criterion"] == "P-TIMER"


def test_trace_correlator_completeness_math() -> None:
    """SLO-OBS-01：trace 关联完整率 = 关联事件数 / 总事件数，阈值 ≥99%。"""
    correlator = TraceCorrelator()
    correlator.record("step.completed", trace_id="t1", run_id="r1", tenant_id="tenant-a")
    correlator.record("step.completed", trace_id="t1", run_id="r1", tenant_id="tenant-a")
    correlator.record("step.failed", trace_id="", run_id="r1", tenant_id="tenant-a")  # 缺 trace_id
    assert correlator.total_events == 3
    assert correlator.correlated_events == 2
    assert correlator.completeness() == pytest.approx(2 / 3)

    full = TraceCorrelator()
    for i in range(100):
        full.record("step.completed", trace_id=f"t{i}", run_id="r1", tenant_id="tenant-a")
    full.assert_slo_obs01()  # 100% ≥ 99% 不抛

    gapped = TraceCorrelator()
    for i in range(100):
        gapped.record(
            "step.completed",
            trace_id=f"t{i}" if i < 98 else "",
            run_id="r1",
            tenant_id="tenant-a",
        )
    with pytest.raises(AssertionError, match="SLO-OBS-01"):
        gapped.assert_slo_obs01()  # 98% < 99% 必须抛


def test_retention_mock_blocks_delete_with_active_refs() -> None:
    """P-PIN mock：active 引用存在时防删；release 后允许删除。"""
    guard = MockRetentionGuard()
    guard.acquire(resource_type="skill", resource_id="search", version="3", run_id="r1")

    with pytest.raises(RetentionBlockedError):
        guard.assert_delete_allowed(resource_type="skill", resource_id="search", version="3")

    guard.release(resource_type="skill", resource_id="search", version="3", run_id="r1")
    guard.assert_delete_allowed(resource_type="skill", resource_id="search", version="3")
    # 其他版本不受影响
    guard.assert_delete_allowed(resource_type="skill", resource_id="search", version="4")


@pytest.mark.parametrize("merge", [merge_dbos_evidence, merge_restate_evidence])
def test_evidence_writer_merges_existing_criteria(
    tmp_path: Path,
    merge: Callable[[dict[str, object], Path], dict[str, object]],
) -> None:
    """局部 PoC 模块落盘时保留既有口径，当前运行结果优先。"""
    path = tmp_path / "evidence.json"
    path.write_text(
        json.dumps({"criteria": {"P-CRASH": {"passed": True}, "P-PIN": {"passed": False}}}),
        encoding="utf-8",
    )

    merged = merge(
        {"criteria": {"P-PIN": {"passed": True}, "P-SIGNAL": {"passed": True}}},
        path,
    )

    assert set(merged) == {"P-CRASH", "P-PIN", "P-SIGNAL"}
    assert merged["P-PIN"] == {"passed": True}
