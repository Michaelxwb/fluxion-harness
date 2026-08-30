"""TASK-005（Phase 6）本地状态审计（FEAT-P6-05 ③，Gate G5 / S-09）。

真实边界：AST 扫描真实 `backend/src/fluxion/runtime/` 源码的全部模块级/实例级
可变容器（dict/list/set/deque），对照标注表逐项分类（无 mock）。

断言：
- 全部容器已标注（未标注=失败）；
- Durable/SoT 本地命中=0（durable 事实必须外置 Store，RULE-P6-05）；
- Scheduler/Trace/Workflow Stub 覆盖检查（G5 全覆盖要求）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from local_state_audit import audit


class TestS09LocalStateAudit:
    def test_s09_all_local_state_annotated_no_durable_sot(self) -> None:
        """S-09[integration]：Runtime 进程内全部本地状态标注 + Durable/SoT 命中=0。"""
        result = audit()

        assert result["total_containers"] > 0, "审计应发现本地容器（扫描失效即失败）"
        assert result["unannotated"] == [], f"存在未标注容器: {result['unannotated']}"
        assert result["durable_sot_hits"] == [], (
            f"Durable/SoT 本地命中（durable 事实必须外置）: {result['durable_sot_hits']}"
        )
        assert result["missing_required"] == [], (
            f"覆盖检查失败（Scheduler/Trace/Workflow Stub 必须在标注表）: {result['missing_required']}"
        )
        assert result["passed"] is True

    def test_s09_negative_probes_detected(self) -> None:
        """review 复审收尾：负向探针——注入违规源码必须被审计捕获。

        三类探针（临时写入 runtime/ → 断言命中 → 清理）：
        - R1 durable/SoT 语义命名（LocalLedger 类）；
        - R2 本地持久化通道（sqlite3.connect）；
        - 未标注容器（探针类的实例级 dict）。
        """
        import local_state_audit as audit_mod

        probe = audit_mod.RUNTIME_ROOT / "_audit_probe_negative_tmp.py"
        probe.write_text(
            "import sqlite3\n"
            "\n"
            "class LocalLedger:\n"
            "    def __init__(self) -> None:\n"
            "        self._entries: dict[str, object] = {}\n"
            "\n"
            "def _open() -> object:\n"
            "    return sqlite3.connect(\"/tmp/probe.db\")\n",
            encoding="utf-8",
        )
        try:
            result = audit_mod.audit()
            assert any("LocalLedger" in v for v in result["durable_sot_hits"]), (
                f"R1 语义命名探针未被捕获: {result['durable_sot_hits']}"
            )
            assert any("sqlite3.connect" in v for v in result["durable_sot_hits"]), (
                f"R2 本地持久化通道探针未被捕获: {result['durable_sot_hits']}"
            )
            assert any("_entries" in v for v in result["unannotated"]), (
                f"未标注容器探针未被捕获: {result['unannotated']}"
            )
            assert result["passed"] is False, "注入违规后审计必须失败"
        finally:
            probe.unlink(missing_ok=True)

        # 清理后恢复通过
        assert audit_mod.audit()["passed"] is True

    def test_s09_scheduler_trace_covered(self) -> None:
        """S-09 附属：G5 覆盖检查——Scheduler/Trace 关键容器在标注表。

        Workflow Stub 已移出主模块（TASK-009），不再进入生产 local-state 扫描范围。
        """
        result = audit()
        ephemeral = set(result["by_classification"]["Ephemeral"])
        assert "scheduler.py:RuntimeScheduler._tasks" in ephemeral, "Scheduler._tasks 必须标注"
        assert "tracing.py:InMemoryTraceStore._records" in ephemeral, "Trace store 必须标注"
