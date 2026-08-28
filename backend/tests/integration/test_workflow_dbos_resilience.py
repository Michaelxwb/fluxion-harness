"""E-01（integration）：真实 DBOS backend 停机 → ResilientWorkflowEngine 熔断快速失败。

真实边界：不可达端口上的真实连接拒绝（非 mock 故障注入）+ 真实 DbosWorkflowEngine
（有界 launch/op timeout 封装 DBOS 内部无限重试）+ 真实 ResilientWorkflowEngine
熔断器。行为时间线由子进程 runner 产出（DBOS 同进程仅一个全局实例，主 pytest
进程不可复用 bad URL 构造引擎）。

断言（design §2.5.2 E-01）：
- 每次失败均为 WorkflowBackendUnavailableError（code 40_104），有界耗时（非 hang）；
- threshold 次失败后 breaker open，后续调用 fast-fail（elapsed ≈ 0）；
- cooldown 到期后试探恢复（真实尝试，非 fast-fail）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from fluxion.errors.workflow import WORKFLOW_BACKEND_UNAVAILABLE

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_RUNNER_MODULE = "tests.workflow_runtime.e01_breaker_runner"


def _run_runner(timeout: float = 90.0) -> dict[str, object]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_BACKEND_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [sys.executable, "-m", _RUNNER_MODULE],
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
        pytest.fail(f"E-01 runner 超时（hang，违反规则 18）；输出:\n" + "\n".join(lines))
    reader.join(timeout=5.0)
    for line in lines:
        if line.startswith("E01_RESULT "):
            return json.loads(line[len("E01_RESULT ") :])
    pytest.fail(f"E-01 runner 未产出结果行；输出:\n" + "\n".join(lines[-40:]))


def test_e01_backend_down_breaker_opens_and_recovers() -> None:
    result = _run_runner()
    timeline = result["timeline"]
    assert isinstance(timeline, list) and len(timeline) == 5

    # 阶段 1：threshold 次真实尝试均快速失败（有界、明确错误码，非 hang）
    attempts = timeline[:3]
    for attempt in attempts:
        assert attempt["outcome"] == "backend-unavailable", attempt
        assert attempt["code"] == WORKFLOW_BACKEND_UNAVAILABLE, attempt
        assert attempt["fast_fail"] is False, attempt
        assert attempt["elapsed"] < 5.0, attempt  # DBOS 内部重试被引擎层有界封装
    assert attempts[-1]["breaker_open"] is True, attempts  # 第 3 次失败后熔断打开

    # 阶段 1 收尾：open 状态下 fast-fail（elapsed ≈ 0）
    fast_fail = timeline[3]
    assert fast_fail["outcome"] == "backend-unavailable", fast_fail
    assert fast_fail["code"] == WORKFLOW_BACKEND_UNAVAILABLE, fast_fail
    assert fast_fail["fast_fail"] is True, fast_fail
    assert fast_fail["elapsed"] < 0.5, fast_fail
    assert fast_fail["breaker_open"] is True, fast_fail

    # 阶段 2：cooldown 到期后试探恢复——真实尝试穿透（错误为 launch/backend
    # 失败而非 "circuit breaker open" fast-fail）
    probe = timeline[4]
    assert probe["outcome"] == "backend-unavailable", probe
    assert probe["code"] == WORKFLOW_BACKEND_UNAVAILABLE, probe
    assert probe["fast_fail"] is False, probe
