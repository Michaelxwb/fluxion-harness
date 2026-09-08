"""TASK-002（P1-02）：Post/Terminal Hook 强制 FAIL_OPEN。

真实边界：真实 TypedEventBus/HookScheduler（S-FP-01 注册期矩阵）＋真实
service（dev bundle＋PG，经 tests.runtime_helpers 共享 harness）全链路 run。
审计示例插件全 FAIL_OPEN，本矩阵不影响既有 E-02（before_* FAIL_CLOSED 照常阻断）。
"""

from __future__ import annotations

import asyncio

import pytest

from fluxion.kernel.events import (
    AfterExecutionPayload,
    AfterModelCallPayload,
    AfterToolCallPayload,
    BeforeExecutionPayload,
    BeforeModelCallPayload,
    BeforeToolCallPayload,
    EventPayload,
    FailPolicy,
    HookRegistration,
    OnExecutionCancelledPayload,
    OnExecutionErrorPayload,
    TypedEventBus,
)
from tests.runtime_helpers import hook_run_request, hook_test_service

_POST_PAYLOADS = (
    AfterModelCallPayload,
    AfterToolCallPayload,
    AfterExecutionPayload,
    OnExecutionErrorPayload,
    OnExecutionCancelledPayload,
)
_PRE_PAYLOADS = (
    BeforeExecutionPayload,
    BeforeModelCallPayload,
    BeforeToolCallPayload,
)


def _registration(
    registration_id: str,
    event_type: type[EventPayload],
    handler,  # type: ignore[no-untyped-def]
    *,
    fail_policy: FailPolicy = FailPolicy.FAIL_CLOSED,
) -> HookRegistration:
    return HookRegistration(
        registration_id=registration_id,
        event_type=event_type,
        priority=1,
        timeout_ms=1000,
        fail_policy=fail_policy,
        handler=handler,
    )


async def _noop(_payload: EventPayload) -> None:
    return None


async def _boom(_payload: EventPayload) -> None:
    raise ValueError("post-hook-boom")


@pytest.mark.asyncio
async def test_S_FP_01_post_hook_fail_closed_rejected_at_registration() -> None:
    """S-FP-01：Post/Terminal 点位注册 FAIL_CLOSED 被拒绝；Before 点位仍可 CLOSED。

    另做完备性：全部 EventPayload 具体子类恰好落入 PRE∪POST，无漏网新点位。
    """
    bus = TypedEventBus()
    for payload_type in _POST_PAYLOADS:
        with pytest.raises(ValueError, match="FAIL_OPEN"):
            bus.register(
                _registration(f"closed-{payload_type.__name__}", payload_type, _noop)
            )
    for payload_type in _PRE_PAYLOADS:
        bus.register(
            _registration(f"closed-{payload_type.__name__}", payload_type, _noop)
        )
    classified = set(_PRE_PAYLOADS) | set(_POST_PAYLOADS)
    # 注：payload 全用 @dataclass(slots=True)，装饰器重建类对象导致同名类在
    # __subclasses__() 中出现两次——按类名去重比较，只关心"无漏网新点位"。
    assert {c.__name__ for c in EventPayload.__subclasses__()} == {
        c.__name__ for c in classified
    }


@pytest.mark.asyncio
async def test_S_FP_02_after_tool_failure_does_not_change_result() -> None:
    """S-FP-02：after_tool 失败 → tool 成功结果对外不变，execution 继续成功。"""
    bus = TypedEventBus()
    bus.register(
        _registration(
            "after-tool-boom",
            AfterToolCallPayload,
            _boom,
            fail_policy=FailPolicy.FAIL_OPEN,
        )
    )
    service, _store = await hook_test_service(bus)
    try:
        result = await service.run(hook_run_request())
        assert any(
            tool.get("tool_id") == "time.now" for tool in result.tool_results
        ), result.tool_results
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_S_FP_03_after_execution_failure_keeps_completed() -> None:
    """S-FP-03：after_execution 失败 → 已 COMPLETED 终态不变，对外仍成功。"""
    bus = TypedEventBus()
    bus.register(
        _registration(
            "after-execution-boom",
            AfterExecutionPayload,
            _boom,
            fail_policy=FailPolicy.FAIL_OPEN,
        )
    )
    service, _store = await hook_test_service(bus)
    try:
        result = await service.run(hook_run_request())
        trace = await service.trace_store.get(result.trace_id)
        assert trace is not None
        assert trace.status == "completed", trace.status
        assert trace.error is None
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_S_FP_04_error_hook_failure_does_not_mask_business_error() -> None:
    """S-FP-04：on_execution_error 自身失败 → 不覆盖原业务异常。"""
    from fluxion.services.runtime_app import RuntimeApplicationError, ToolCallRequest

    bus = TypedEventBus()
    bus.register(
        _registration(
            "on-error-boom",
            OnExecutionErrorPayload,
            _boom,
            fail_policy=FailPolicy.FAIL_OPEN,
        )
    )
    service, _store = await hook_test_service(bus)
    try:
        with pytest.raises(RuntimeApplicationError) as exc_info:
            await service.run(
                hook_run_request(
                    session_id="session-fp04",
                    tool_calls=[ToolCallRequest(tool_id="calc.eval", arguments={})],
                )
            )
        assert exc_info.value.code == "tool_not_allowed", exc_info.value.code
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_S_FP_05_cancelled_hook_failure_does_not_mask_cancellation() -> None:
    """S-FP-05：on_execution_cancelled 自身失败 → 不覆盖 cancellation。"""
    bus = TypedEventBus()

    async def _cancel(_payload: BeforeModelCallPayload) -> None:
        raise asyncio.CancelledError

    bus.register(_registration("cancel-hook", BeforeModelCallPayload, _cancel))
    bus.register(
        _registration(
            "on-cancelled-boom",
            OnExecutionCancelledPayload,
            _boom,
            fail_policy=FailPolicy.FAIL_OPEN,
        )
    )
    service, _store = await hook_test_service(bus)
    try:
        with pytest.raises(asyncio.CancelledError):
            await service.run(hook_run_request(session_id="session-fp05"))
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_E_FP_01_write_tool_not_retried_on_after_failure() -> None:
    """E-FP-01：after 失败 → 调用方仍成功且工具恰好执行一次（不诱发重试）。

    以 time.now 为 stand-in：after 失败与工具副作用正交的机制对读写工具一致。
    """
    calls: list[str] = []
    bus = TypedEventBus()

    async def _counting_before(payload: BeforeToolCallPayload) -> None:
        calls.append(payload.tool_id)

    bus.register(_registration("count-before", BeforeToolCallPayload, _counting_before))
    bus.register(
        _registration(
            "after-tool-boom",
            AfterToolCallPayload,
            _boom,
            fail_policy=FailPolicy.FAIL_OPEN,
        )
    )
    service, _store = await hook_test_service(bus)
    try:
        result = await service.run(hook_run_request(session_id="session-fp-e01"))
        assert result.tool_results, "调用方必须看到成功（含 tool 结果）"
        assert calls == ["time.now"], calls
    finally:
        await service.close()
