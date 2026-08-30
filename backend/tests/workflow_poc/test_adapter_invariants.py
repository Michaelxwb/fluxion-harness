"""B-01 / E-01 验收测试（ADR-WF-001 TASK-001 契约）。

- B-01 (unit): WorkflowAdapter 不持有本地 durable state（RULE-WF-01 / RISK-WF-02）
- E-01 (integration): backend 不可达注入下 Adapter fail policy + circuit-breaker
  真实逻辑返回定义错误码（非 hang、非裸异常），连续失败后熔断打开（RULE-WF-04，规则 18）
"""

from __future__ import annotations

import time

import pytest
from tests.runtime_helpers import runtime_context

from fluxion.errors.workflow import (
    WORKFLOW_BACKEND_UNAVAILABLE,
    WorkflowBackendUnavailableError,
)
from tests.fakes.workflow import StubWorkflowEngine

from fluxion.runtime.workflow import (
    FailPolicy,
    ResilientWorkflowEngine,
    WorkflowAdapter,
    WorkflowStartRequest,
)


async def test_b01_local_durable_state_zero() -> None:
    """B-01: Protocol 全成员路径后 Adapter 本地 durable state 恒为 0。"""
    engine = StubWorkflowEngine(run_id="wf-run-b01")
    adapter = WorkflowAdapter(workflow_id="weekly-report", engine=engine)

    assert adapter.local_durable_state_count == 0

    # Protocol 扩展成员（resume/signal/cancel/get_status）逐个走通后不变量仍成立。
    request = WorkflowStartRequest(
        workflow_id="weekly-report",
        tenant_id="tenant-a",
        user_id="user-a",
        execution_id="exec-b01",
        trace_id="trace-b01",
        arguments={"topic": "revenue"},
    )
    await engine.start(request)
    assert adapter.local_durable_state_count == 0

    status = await engine.resume("wf-run-b01")
    assert status.run_id == "wf-run-b01"
    await engine.signal("wf-run-b01", "approve", {"approved": True})
    await engine.cancel("wf-run-b01", timeout=0.5)
    assert (await engine.get_status("wf-run-b01")).run_id == "wf-run-b01"
    assert adapter.local_durable_state_count == 0


class UnreachableEngine:
    """fault-injection：backend 连接层不可达（抛 ConnectionError，非业务错误）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def start(self, request: WorkflowStartRequest) -> object:
        self.calls += 1
        raise ConnectionError("workflow backend unreachable")

    async def resume(self, run_id: str) -> object:
        self.calls += 1
        raise ConnectionError("workflow backend unreachable")

    async def signal(self, run_id: str, name: str, payload: object) -> None:
        self.calls += 1
        raise ConnectionError("workflow backend unreachable")

    async def cancel(self, run_id: str, *, timeout: float) -> None:
        self.calls += 1
        raise ConnectionError("workflow backend unreachable")

    async def get_status(self, run_id: str) -> object:
        self.calls += 1
        raise ConnectionError("workflow backend unreachable")


async def test_e01_fail_policy_circuit_breaker() -> None:
    """E-01: 不可达 backend → 定义错误码（有界耗时）；阈值后熔断快速失败。"""
    unreachable = UnreachableEngine()
    engine = ResilientWorkflowEngine(
        delegate=unreachable,
        policy=FailPolicy(
            timeout_seconds=0.5,
            max_retries=1,
            retry_delay_seconds=0.01,
            breaker_threshold=2,
            breaker_cooldown_seconds=60.0,
        ),
    )
    adapter = WorkflowAdapter(workflow_id="poc-weekly-report", engine=engine)
    context, _runtime = await runtime_context()

    # 第 1 次调用：1 次尝试 + 1 次 retry 均失败 → 定义错误码，耗时有限（非 hang）。
    started = time.monotonic()
    with pytest.raises(WorkflowBackendUnavailableError) as exc_info:
        await adapter.execute(context, {"topic": "revenue"})
    assert time.monotonic() - started < 5.0
    assert exc_info.value.code == WORKFLOW_BACKEND_UNAVAILABLE
    assert unreachable.calls == 2

    # 第 2 次调用：连续失败达到 breaker_threshold → 熔断打开。
    with pytest.raises(WorkflowBackendUnavailableError):
        await adapter.execute(context, {"topic": "revenue"})

    # 第 3 次调用：熔断打开，快速失败——不再触达 delegate、无 retry 等待。
    started = time.monotonic()
    with pytest.raises(WorkflowBackendUnavailableError):
        await adapter.execute(context, {"topic": "revenue"})
    assert time.monotonic() - started < 0.2
    assert unreachable.calls == 2


async def test_e01_timeout_wrapped_as_defined_error() -> None:
    """E-01: 单尝试超时被包装为定义错误码，不向调用方泄漏裸 TimeoutError。"""
    import asyncio

    class HangingEngine:
        async def start(self, request: WorkflowStartRequest) -> object:
            await asyncio.sleep(30)

        async def resume(self, run_id: str) -> object:
            await asyncio.sleep(30)

        async def signal(self, run_id: str, name: str, payload: object) -> None:
            await asyncio.sleep(30)

        async def cancel(self, run_id: str, *, timeout: float) -> None:
            await asyncio.sleep(30)

        async def get_status(self, run_id: str) -> object:
            await asyncio.sleep(30)

    engine = ResilientWorkflowEngine(
        delegate=HangingEngine(),
        policy=FailPolicy(
            timeout_seconds=0.2,
            max_retries=0,
            retry_delay_seconds=0.0,
            breaker_threshold=3,
            breaker_cooldown_seconds=60.0,
        ),
    )
    adapter = WorkflowAdapter(workflow_id="poc-weekly-report", engine=engine)
    context, _runtime = await runtime_context()

    started = time.monotonic()
    with pytest.raises(WorkflowBackendUnavailableError) as exc_info:
        await adapter.execute(context, {"topic": "revenue"})
    elapsed = time.monotonic() - started

    assert elapsed < 2.0  # timeout(0.2) 有界返回，非无限等待
    assert exc_info.value.code == WORKFLOW_BACKEND_UNAVAILABLE
