"""DBOS PoC 1000-concurrent baseline（ADR-WF-001 TASK-003 P-SCALE+，roadmap TASK-0002 项 8）。

首个跑通候选执行（其余候选可选）；数据写入 `evidence/dbos.json` 的 `baseline` 字段。
"""

from __future__ import annotations

import asyncio
import time
import uuid

from dbos import DBOS, SetWorkflowID

from tests.workflow_poc.dbos_app import (
    DBOSWorkflowEngine,
    attach_correlator,
    launch_dbos,
    poc_baseline_workflow,
    reset_business_tables,
    resolve_db_url,
)
from tests.workflow_poc.dbos_testing import record_evidence, write_dbos_evidence
from tests.workflow_poc.poc_workflow import TraceCorrelator

WORKFLOW_COUNT = 1000
P95_INDEX = 949  # ceil(0.95 * 1000) - 1


async def test_pscale_baseline_1000_concurrent() -> None:
    """1000 并发 workflow 启动/推进：start P95≤1s（SLO-WF-01）且全部 SUCCESS。"""
    engine = DBOSWorkflowEngine(listen=[])
    attach_correlator(TraceCorrelator())
    reset_business_tables(resolve_db_url())
    try:
        launch_dbos()
        tag = uuid.uuid4().hex[:6]
        latencies_ms: list[float] = []
        handles = []
        started_at = time.monotonic()
        for index in range(WORKFLOW_COUNT):
            run_id = f"baseline-{tag}-{index:04d}"
            mark = time.monotonic()
            with SetWorkflowID(run_id):
                handle = DBOS.start_workflow(
                    poc_baseline_workflow, run_id, "tenant-baseline", f"trace-{run_id}"
                )
            latencies_ms.append((time.monotonic() - mark) * 1000)
            handles.append(handle)

        results = await asyncio.gather(
            *[
                asyncio.wait_for(
                    asyncio.to_thread(DBOS.get_result, handle.get_workflow_id()), timeout=600.0
                )
                for handle in handles
            ]
        )
        total_seconds = time.monotonic() - started_at

        assert len(results) == WORKFLOW_COUNT
        p95_ms = sorted(latencies_ms)[P95_INDEX]
        assert p95_ms <= 1000.0  # SLO-WF-01：durable start P95 ≤ 1s
        assert len(engine.list_records("tenant-baseline")) == WORKFLOW_COUNT  # 每个都有 durable 产物

        record_evidence(
            "baseline",
            passed=True,
            detail=f"{WORKFLOW_COUNT} 并发 workflow 全部 SUCCESS；单进程 start 吞吐见 metrics",
            metrics={
                "count": WORKFLOW_COUNT,
                "start_p95_ms": round(p95_ms, 1),
                "start_max_ms": round(max(latencies_ms), 1),
                "total_seconds": round(total_seconds, 1),
                "start_throughput_per_sec": round(WORKFLOW_COUNT / total_seconds, 1),
            },
        )
    finally:
        reset_business_tables(resolve_db_url())
        write_dbos_evidence()
