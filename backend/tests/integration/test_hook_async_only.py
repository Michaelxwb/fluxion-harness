"""TASK-006（P2-02）：Hook Handler 收敛为 async-only。

真实边界：真实 HookRegistration 构造校验＋真实 TypedEventBus.dispatch＋
计时断言。sync handler 注册期拒绝；async 超时走 cooperative cancellation；
总线不再 spawn 线程（此前 to_thread 分支已删）。
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from fluxion.kernel.events import (
    BeforeToolCallPayload,
    EventPayload,
    FailPolicy,
    HookRegistration,
    HookStatus,
    TypedEventBus,
)


def _payload() -> BeforeToolCallPayload:
    return BeforeToolCallPayload(
        tenant_id="tenant-a",
        execution_id="execution-a",
        trace_id="trace-a",
        tool_id="deploy",
        arguments={},
    )


def _registration(
    registration_id: str,
    handler,  # type: ignore[no-untyped-def]
    *,
    timeout_ms: int = 1000,
    fail_policy: FailPolicy = FailPolicy.FAIL_OPEN,
) -> HookRegistration:
    return HookRegistration(
        registration_id=registration_id,
        event_type=BeforeToolCallPayload,
        priority=1,
        timeout_ms=timeout_ms,
        fail_policy=fail_policy,
        handler=handler,
    )


@pytest.mark.asyncio
async def test_S_SYNC_01_sync_handler_rejected_at_registration() -> None:
    """S-SYNC-01：sync handler 构造注册时明确失败。"""

    def _sync(_payload: EventPayload) -> None:
        return None

    with pytest.raises(ValueError, match="async"):
        _registration("sync-hook", _sync)


@pytest.mark.asyncio
async def test_S_SYNC_02_async_timeout_cancels_handler() -> None:
    """S-SYNC-02：async hook 超时 → handler 任务被取消，主流程按 FAIL_OPEN 继续。"""
    cancelled: list[bool] = []

    async def _slow(_payload: EventPayload) -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    bus = TypedEventBus()
    bus.register(_registration("slow-open", _slow, timeout_ms=1))
    results = await bus.dispatch(_payload())
    assert len(results) == 1
    assert results[0].status == HookStatus.TIMEOUT
    assert cancelled == [True], "超时必须取消 handler 协程（cooperative）"


@pytest.mark.asyncio
async def test_S_SYNC_03_no_thread_spawned_for_hooks() -> None:
    """S-SYNC-03：hook 分发不 spawn 线程（to_thread 分支已删），无残留。"""
    bus = TypedEventBus()

    async def _fast(_payload: EventPayload) -> None:
        return None

    async def _slow(_payload: EventPayload) -> None:
        await asyncio.sleep(0.05)

    bus.register(_registration("fast", _fast))
    bus.register(_registration("slow-timeout", _slow, timeout_ms=1))
    before = {thread.ident for thread in threading.enumerate()}
    await bus.dispatch(_payload())
    after = {thread.ident for thread in threading.enumerate()}
    assert after <= before, "分发前后线程集合不得新增"
