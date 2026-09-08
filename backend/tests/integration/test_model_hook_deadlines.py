"""TASK-007：Model Hook 不消耗业务 deadline，所有真实 attempt 成对收尾。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from fluxion.kernel.events import (
    AfterModelCallPayload,
    FailPolicy,
    HookRegistration,
    TypedEventBus,
)
from fluxion.plugins.contracts import ModelProviderError, ModelRequest, ModelResponse
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.runtime.agent import AgentLoopTimeoutError
from fluxion.services.runtime_app import RuntimeApplicationService, _ModelCallHookBridge
from tests.integration.test_model_hook_boundaries import (
    _context,
    _RecordingObserver,
    _registry,
    _route,
    _runtime,
    _snapshot,
)
from tests.runtime_helpers import TEST_POSTGRES_DSN


class _DelayedProvider:
    def __init__(
        self,
        delay: float,
        result: ModelResponse | Exception,
        *,
        tokens: tuple[str, ...] = (),
    ) -> None:
        self.delay = delay
        self.result = result
        self.tokens = tokens
        self.started = asyncio.Event()

    async def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        self.started.set()
        await asyncio.sleep(self.delay)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def stream(self, request: ModelRequest) -> AsyncIterator[str]:
        del request
        self.started.set()
        for token in self.tokens:
            yield token
        await asyncio.sleep(self.delay)


def _deadline_snapshot(deadline_ms: int, *provider_ids: str):
    snapshot = _snapshot([_route(item) for item in provider_ids])
    return snapshot.model_copy(
        update={
            "model_resolution": snapshot.model_resolution.model_copy(
                update={"model_deadline_ms": deadline_ms, "model_timeout_ms": 1000}
            )
        }
    )


@pytest.mark.asyncio
async def test_S_MB_01() -> None:
    """慢 FAIL_OPEN after hook 使用自己的 timeout，不吃模型业务 deadline。"""
    bus = TypedEventBus()

    async def _slow_post(_payload: AfterModelCallPayload) -> None:
        await asyncio.sleep(0.05)

    bus.register(
        HookRegistration(
            registration_id="slow-post",
            event_type=AfterModelCallPayload,
            priority=1,
            timeout_ms=200,
            fail_policy=FailPolicy.FAIL_OPEN,
            handler=_slow_post,
        )
    )
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    service = RuntimeApplicationService.create_dev_bundle(store, event_bus=bus)
    provider = _DelayedProvider(
        0,
        ModelResponse(provider_id="stub", content="ok"),
    )
    runtime = _runtime(_registry(stub=provider))
    context = _context(_deadline_snapshot(20, "stub"))
    result = await runtime.run_step(
        context,
        "hi",
        model_call_observer=_ModelCallHookBridge(service, context),
    )
    assert result.output == "ok"
    await service.close()


@pytest.mark.asyncio
async def test_S_MB_02() -> None:
    """总 deadline、外部取消和流关闭都为已开始调用补一条 after。"""
    slow = _DelayedProvider(30, ModelResponse(provider_id="slow", content="late"))
    observer = _RecordingObserver()
    with pytest.raises(AgentLoopTimeoutError):
        await _runtime(_registry(slow=slow)).run_step(
            _context(_deadline_snapshot(10, "slow")),
            "deadline",
            model_call_observer=observer,
        )
    assert len(observer.befores) == len(observer.afters) == 1
    assert observer.afters[0].status == "timeout"

    cancelled_provider = _DelayedProvider(
        30, ModelResponse(provider_id="cancelled", content="late")
    )
    cancelled_observer = _RecordingObserver()
    task = asyncio.create_task(
        _runtime(_registry(cancelled=cancelled_provider)).run_step(
            _context(_deadline_snapshot(1000, "cancelled")),
            "cancel",
            model_call_observer=cancelled_observer,
        )
    )
    await cancelled_provider.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(cancelled_observer.befores) == len(cancelled_observer.afters) == 1
    assert cancelled_observer.afters[0].status == "error"

    streaming = _DelayedProvider(
        30,
        ModelResponse(provider_id="stream", content=""),
        tokens=("one",),
    )
    stream_observer = _RecordingObserver()
    generator = _runtime(_registry(stream=streaming)).stream_final_answer(
        _context(_deadline_snapshot(1000, "stream")),
        "stream",
        model_call_observer=stream_observer,
    )
    assert await anext(generator) == "one"
    await generator.aclose()
    assert len(stream_observer.befores) == len(stream_observer.afters) == 1
    assert stream_observer.afters[0].status == "error"


@pytest.mark.asyncio
async def test_E_MB_01() -> None:
    """failover 的 Provider 耗时累计计算，不能为每个 attempt 重置总 deadline。"""
    first = _DelayedProvider(0.06, ModelProviderError("first failed"))
    second = _DelayedProvider(
        0.06,
        ModelResponse(provider_id="second", content="too late"),
    )
    observer = _RecordingObserver()
    with pytest.raises(AgentLoopTimeoutError):
        await _runtime(_registry(first=first, second=second)).run_step(
            _context(_deadline_snapshot(90, "first", "second")),
            "budget",
            model_call_observer=observer,
        )
    assert [item.status for item in observer.afters] == ["error", "timeout"]
    assert len(observer.befores) == len(observer.afters) == 2

