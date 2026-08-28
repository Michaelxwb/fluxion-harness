"""TASK-004 验收（S-10 / S-04 / E-03）：durable graph 解释器真实运行。

真实边界（不 mock 引擎/存储）：
- 全部场景经独立子进程 runner 执行：真实 DbosWorkflowEngine → DBOS 2.31 →
  本地 PostgreSQL（fluxion_workflow 库，含 DBOS sys schema）；
- capability executor 为真实副作用执行器（psycopg 幂等写 wf_test_records），
  E-03 的「业务写不重复」由业务表行数与执行计数证明；
- 节点 timeout（S-04）与 step durable retry（E-03）均为 DBOS 真实语义。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_RUNNER_MODULE = "tests.workflow_runtime.graph_runner"


def _run_scenario(scenario: str, *, timeout: float = 180.0) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_BACKEND_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            _RUNNER_MODULE,
            "run",
            "--scenario",
            scenario,
            "--execution-id",
            f"exec-{uuid.uuid4().hex[:8]}",
        ],
        cwd=str(_BACKEND_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines: list[str] = []
    reader = threading.Thread(
        target=lambda: lines.extend(line.rstrip("\n") for line in process.stdout or []),
        daemon=True,
    )
    reader.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        pytest.fail(f"runner 超时（hang，违反规则 18）；输出:\n" + "\n".join(lines[-40:]))
    reader.join(timeout=5.0)
    for line in lines:
        if line.startswith("RUN_RESULT "):
            return json.loads(line[len("RUN_RESULT ") :])
        if line.startswith("RUN_FAILED "):
            return json.loads(line[len("RUN_FAILED ") :])
    pytest.fail(f"runner 未产出结果行；输出:\n" + "\n".join(lines[-40:]))


def test_s10_mixed_graph_routes_joins_and_nests() -> None:
    """S-10（E2E）：condition/switch 语义路由、parallel 并发汇聚、transform、subworkflow。

    断言：
    - gold 分支执行、std 分支被剪枝（node_states）；
    - transform 输出插值正确；
    - subworkflow 输出来自子流程节点；
    - 两个 0.6s 并行分支总耗时 < 1.0s（并发完成，非串行）。
    """
    outcome = _run_scenario("s10")
    assert outcome["status"] == "SUCCESS", outcome
    result = outcome["result"]
    outputs = result["outputs"]
    node_states = result["node_states"]

    assert outputs["fetch"] == {"tier": "gold"}
    assert outputs["branch"]["condition"] is True
    assert outputs["branch"]["next"] == ["gold_setup"]
    assert node_states["gold_setup"] == "succeeded"
    assert node_states["std_setup"] == "skipped", "未选中分支必须被剪枝"
    assert outputs["brief"] == "tier=gold"
    assert outputs["child"]["outputs"]["child_step"] == {"greeting": "hello"}
    assert outputs["notify"] == {"done": "hello"}
    fanout = outputs["fanout"]
    assert fanout["join_policy"] == "all"
    assert len(fanout["branches"]) == 2

    # 并行分支并发完成：两个 0.6s 分支的执行窗口重叠
    from tests.workflow_runtime.graph_fixtures import list_records

    records = {r["node_id"]: r for r in list_records(_db_url(), outcome["run_id"])}
    par_a, par_b = records["par_a"], records["par_b"]
    overlap = min(par_a["finished_at"], par_b["finished_at"]) - max(
        par_a["started_at"], par_b["started_at"]
    )
    assert overlap > 0.3, "并行分支应重叠执行（并发），实测重叠 {overlap}s".format(overlap=overlap)
    assert outcome["elapsed_ms"] < 10_000


def test_s04_node_timeout_is_bounded_error() -> None:
    """S-04（integration）：timeout_ms < 实际耗时 → 有界转 ERROR，不无限等待。"""
    outcome = _run_scenario("s04")
    assert outcome["status"] == "ERROR", outcome
    # 3 次 step 尝试 × 300ms + 重试间隔，远小于 5s 实际耗时
    assert 500 <= outcome["elapsed_ms"] < 5000, outcome


def test_e03_step_retry_keeps_business_write_unique() -> None:
    """E-03（integration）：step 首次抛异常 → DBOS durable retry 生效，业务写恰 1 条。"""
    outcome = _run_scenario("e03")
    assert outcome["status"] == "SUCCESS", outcome
    result = outcome["result"]
    flaky = result["outputs"]["flaky_step"]
    assert flaky["flaky"] == "recovered"
    assert flaky["executions"] >= 2, "DBOS step retry 必须实际发生"

    from tests.workflow_runtime.graph_fixtures import list_records

    records = list_records(_db_url(), outcome["run_id"])
    assert len(records) == 1, "业务写不重复（SLO-WF-03）：记录必须恰 1 行"
    assert records[0]["node_id"] == "flaky_step"
    assert records[0]["executions"] >= 2


def _db_url() -> str:
    return os.environ.get(
        "FLUXION_WORKFLOW_TEST_DB_URL",
        "postgresql://mmuser:mmuser@localhost:5432/fluxion_workflow",
    )
