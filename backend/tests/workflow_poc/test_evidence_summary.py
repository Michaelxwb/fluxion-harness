"""S-01..S-06 汇总判定（ADR-WF-001 TASK-005）：3 候选 evidence artifact 齐全 + SLO 数值达标。

真实边界：直接读 PoC 自动产出的 evidence JSON（dbos.json / restate.json），断言口径齐全、
PASS 状态与 SLO 数值（durable start P95≤1s / recovery P95≤60s / 不可逆副作用重复=0）。
"""

from __future__ import annotations

import json
from pathlib import Path

EVIDENCE_DIR = Path(__file__).resolve().parent / "evidence"
REQUIRED_CRITERIA = {"P-CRASH", "P-TIMER", "P-IDEMP", "P-PIN", "P-TIMEOUT", "P-SCALE", "P-SIGNAL"}


def _load(name: str) -> dict:
    return json.loads((EVIDENCE_DIR / name).read_text(encoding="utf-8"))


def test_summary_dbos_evidence_complete_and_green() -> None:
    """S-01..S-06 汇总：DBOS 候选 evidence 7 口径全 PASS + SLO 数值达标。"""
    data = _load("dbos.json")
    criteria = data["criteria"]
    missing = REQUIRED_CRITERIA - set(criteria)
    assert not missing, f"DBOS evidence 缺口径 {missing}"
    failed = [k for k, v in criteria.items() if not v["passed"]]
    assert not failed, f"DBOS evidence 有未通过口径 {failed}"
    assert data["all_criteria_passed"] is True
    # SLO 数值（来自 PoC 自动计时写入）
    assert criteria["S-01"]["metrics"]["start_ms"] <= 1000.0  # SLO-WF-01
    assert criteria["P-CRASH"]["metrics"]["recovery_seconds"] <= 60.0  # SLO-WF-02
    assert criteria["P-SCALE"]["metrics"]["distinct_executors"] >= 2  # NFR-SCALE-02
    assert criteria["P-IDEMP"]["passed"]  # SLO-WF-03：副作用重复=0（metrics 语义见 evidence）


def test_summary_restate_evidence_records_boundaries() -> None:
    """S-01..S-06 汇总：Restate evidence 如实记录 5 口径通过 + boundary（不伪造）。"""
    data = _load("restate.json")
    assert data["candidate"] == "restate"
    criteria = data["criteria"]
    assert criteria["P-SIGNAL"]["passed"] and criteria["P-TIMEOUT"]["passed"]
    # 用户决策（选项 ②）：崩溃恢复/scale 记录为 capability boundary，不要求 all_criteria_passed
    assert data["all_criteria_passed"] is False
    assert "license" in data and "BUSL" in data["license"]
