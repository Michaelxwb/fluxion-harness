"""E-02[integration] PoC evidence CI gate（ADR-WF-001 TASK-005 / RULE-fluxion-dfx-001）。

无有效 PoC evidence artifact 时阻断 WorkflowEngine 生产实现路径：证据必须编码阶段
自动化产出（`evidence/dbos.json` all_criteria_passed=True 等），非事后补。本 gate 是
真实文件检查 + 时效校验；缺失 / 过期 / 未全绿 → gate 非零退出（阻断）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

EVIDENCE_DIR = Path(__file__).resolve().parent / "evidence"
GATE_REQUIRED = {
    "dbos.json": {"candidate": "dbos", "must_pass": True},
    "restate.json": {"candidate": "restate", "must_pass": False},  # Restate 部分 boundary，证据仍须存在
}
MAX_AGE_SECONDS = 7 * 24 * 3600  # evidence 时效：7 天内


def assert_poc_evidence_gate() -> None:
    """E-02 gate：无有效 PoC evidence 时阻断（抛异常=非零退出）。"""
    missing = [name for name in GATE_REQUIRED if not (EVIDENCE_DIR / name).exists()]
    if missing:
        raise AssertionError(f"E-02 gate 阻断：evidence 缺失 {missing}（WorkflowEngine 生产实现路径被锁）")

    for name, spec in GATE_REQUIRED.items():
        path = EVIDENCE_DIR / name
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("candidate") != spec["candidate"]:
            raise AssertionError(f"E-02 gate 阻断：{name} candidate 不符 {data.get('candidate')}")
        age = time.time() - path.stat().st_mtime
        if age > MAX_AGE_SECONDS:
            raise AssertionError(f"E-02 gate 阻断：{name} evidence 过期（{age/3600:.0f}h > {MAX_AGE_SECONDS/3600:.0f}h）")
        if spec["must_pass"] and data.get("all_criteria_passed") is not True:
            raise AssertionError(f"E-02 gate 阻断：{name} all_criteria_passed 非 True（不可信证据）")


def test_e02_gate_passes_on_valid_evidence() -> None:
    """真实边界：当前 dbos.json/restate.json 全绿/存在 → gate 放行。"""
    assert_poc_evidence_gate()


def test_e02_gate_blocks_on_missing_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """真实边界：缺 evidence → gate 阻断（模拟缺失：指向空目录）。"""
    monkeypatch.setattr("tests.workflow_poc.test_poc_gate.EVIDENCE_DIR", tmp_path)
    with pytest.raises(AssertionError, match="evidence 缺失"):
        assert_poc_evidence_gate()
