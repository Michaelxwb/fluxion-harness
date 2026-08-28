"""E-01 runner：真实 DBOS backend 宕机（不可达端口）下 ResilientWorkflowEngine 熔断行为。

以独立子进程运行（避免污染主 pytest 进程的 DBOS 全局实例；DBOS 同进程仅允许
一个实例）。行为时间线以 `E01_RESULT <json>` 单行输出，由
`backend/tests/integration/test_workflow_dbos_resilience.py` 断言。

场景（design §2.5.2 E-01）：
- N 次失败后 breaker open；
- open 期间调用快速失败（非 hang，错误码 40_104）；
- cooldown 到期后试探（真实尝试，非 fast-fail）。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

from fluxion.errors.workflow import (
    WORKFLOW_BACKEND_UNAVAILABLE,
    WorkflowBackendUnavailableError,
)
from fluxion.runtime.workflow import (
    FailPolicy,
    ResilientWorkflowEngine,
    WorkflowStartRequest,
)
from fluxion.runtime.workflow_dbos import DbosWorkflowEngine

# 无人监听端口：真实连接拒绝（backend 停机），非 mock 故障注入。
UNREACHABLE_URL = "postgresql://mmuser:mmuser@127.0.0.1:59999/fluxion_workflow_e01"
BREAKER_THRESHOLD = 3
COOLDOWN_SECONDS = 2.0


def _request(index: int) -> WorkflowStartRequest:
    return WorkflowStartRequest(
        workflow_id="wf-e01",
        tenant_id="tenant-a",
        user_id="user-a",
        execution_id=f"exec-{index}",
        trace_id=f"trace-{index}",
        arguments={},
    )


async def _attempt(resilient: ResilientWorkflowEngine, index: int) -> dict[str, object]:
    started = time.monotonic()
    try:
        await resilient.start(_request(index))
        return {"i": index, "outcome": "unexpected-success"}
    except WorkflowBackendUnavailableError as error:
        return {
            "i": index,
            "outcome": "backend-unavailable",
            "code": error.code,
            "fast_fail": "circuit breaker open" in str(error),
            "elapsed": round(time.monotonic() - started, 3),
            "message": str(error)[:160],
        }
    except Exception as error:  # noqa: BLE001 — 时间线需记录任何意外异常形态
        return {
            "i": index,
            "outcome": type(error).__name__,
            "elapsed": round(time.monotonic() - started, 3),
            "message": str(error)[:160],
        }


async def main() -> int:
    engine = DbosWorkflowEngine(
        database_url=UNREACHABLE_URL,
        launch_timeout_seconds=1.0,
        op_timeout_seconds=1.0,
    )
    resilient = ResilientWorkflowEngine(
        delegate=engine,
        policy=FailPolicy(
            timeout_seconds=2.0,
            max_retries=0,
            retry_delay_seconds=0.0,
            breaker_threshold=BREAKER_THRESHOLD,
            breaker_cooldown_seconds=COOLDOWN_SECONDS,
        ),
    )
    timeline: list[dict[str, object]] = []
    # 阶段 1：连续失败直至熔断打开（threshold 次真实尝试 + 1 次 open 快速失败）
    for index in range(BREAKER_THRESHOLD + 1):
        timeline.append(await _attempt(resilient, index))
        timeline[-1]["breaker_open"] = resilient.breaker_open
    # 阶段 2：cooldown 到期后试探（真实尝试，非 fast-fail）
    await asyncio.sleep(COOLDOWN_SECONDS + 0.3)
    timeline.append(await _attempt(resilient, BREAKER_THRESHOLD + 1))
    timeline[-1]["breaker_open_after_probe"] = resilient.breaker_open
    result = {
        "expected_code": WORKFLOW_BACKEND_UNAVAILABLE,
        "timeline": timeline,
    }
    print("E01_RESULT " + json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
