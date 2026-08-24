from __future__ import annotations

import asyncio
import time
from typing import cast

import pytest

from fluxion.kernel.events import (
    BeforeToolCallPayload,
    FailPolicy,
    HookDispatchError,
    HookRegistration,
    HookScope,
    TypedEventBus,
)
from fluxion.resources import ExecutionSnapshot
from fluxion.runtime import RequestContext, RuntimeContext


def _runtime_context() -> RuntimeContext:
    request = RequestContext(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
        session_id="session-a",
    )
    snapshot = ExecutionSnapshot(
        execution_id=request.execution_id,
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        runtime_profile_id=request.runtime_profile_id,
        runtime_profile_version="1",
        model_resolution={"provider": "stub"},
        trace_id=request.trace_id,
    )
    return RuntimeContext(request=request, snapshot=snapshot)


@pytest.mark.asyncio
async def test_S_R06_hook_priority_order_and_trace_are_recorded() -> None:
    calls: list[str] = []
    bus = TypedEventBus()
    context = _runtime_context()

    async def security_hook(payload: BeforeToolCallPayload) -> None:
        calls.append(f"security:{payload.tool_id}")

    async def audit_hook(payload: BeforeToolCallPayload) -> None:
        calls.append(f"audit:{payload.tool_id}")

    bus.register(
        HookRegistration(
            registration_id="audit",
            event_type=BeforeToolCallPayload,
            priority=20,
            timeout_ms=100,
            fail_policy=FailPolicy.FAIL_CLOSED,
            scope=HookScope.GLOBAL,
            handler=audit_hook,
        )
    )
    bus.register(
        HookRegistration(
            registration_id="security",
            event_type=BeforeToolCallPayload,
            priority=10,
            timeout_ms=100,
            fail_policy=FailPolicy.FAIL_CLOSED,
            scope=HookScope.GLOBAL,
            handler=security_hook,
        )
    )

    results = await bus.dispatch(
        BeforeToolCallPayload(
            tenant_id="tenant-a",
            execution_id=context.snapshot.execution_id,
            trace_id=context.snapshot.trace_id,
            tool_id="search",
            arguments={"q": "fluxion"},
        ),
        trace_sink=context,
    )

    assert calls == ["security:search", "audit:search"]
    assert [result.registration_id for result in results] == ["security", "audit"]
    assert all(result.status == "ok" for result in results)
    assert [event.name for event in context.trace].count("hook.completed") == 2


@pytest.mark.asyncio
async def test_E_R06_timeout_fail_policy_controls_dispatch_flow() -> None:
    payload = BeforeToolCallPayload(
        tenant_id="tenant-a",
        execution_id="execution-a",
        trace_id="trace-a",
        tool_id="deploy",
        arguments={},
    )
    fail_open_calls: list[str] = []
    fail_open_bus = TypedEventBus()

    async def slow_open(_payload: BeforeToolCallPayload) -> None:
        await asyncio.sleep(0.05)

    async def after_open(_payload: BeforeToolCallPayload) -> None:
        fail_open_calls.append("after")

    fail_open_bus.register(
        HookRegistration(
            registration_id="slow-open",
            event_type=BeforeToolCallPayload,
            priority=1,
            timeout_ms=1,
            fail_policy=FailPolicy.FAIL_OPEN,
            scope=HookScope.GLOBAL,
            handler=slow_open,
        )
    )
    fail_open_bus.register(
        HookRegistration(
            registration_id="after-open",
            event_type=BeforeToolCallPayload,
            priority=2,
            timeout_ms=100,
            fail_policy=FailPolicy.FAIL_CLOSED,
            scope=HookScope.GLOBAL,
            handler=after_open,
        )
    )

    open_results = await fail_open_bus.dispatch(payload)
    assert [result.status for result in open_results] == ["timeout", "ok"]
    assert fail_open_calls == ["after"]

    fail_closed_calls: list[str] = []
    fail_closed_bus = TypedEventBus()

    async def slow_closed(_payload: BeforeToolCallPayload) -> None:
        await asyncio.sleep(0.05)

    async def after_closed(_payload: BeforeToolCallPayload) -> None:
        fail_closed_calls.append("after")

    fail_closed_bus.register(
        HookRegistration(
            registration_id="slow-closed",
            event_type=BeforeToolCallPayload,
            priority=1,
            timeout_ms=1,
            fail_policy=FailPolicy.FAIL_CLOSED,
            scope=HookScope.GLOBAL,
            handler=slow_closed,
        )
    )
    fail_closed_bus.register(
        HookRegistration(
            registration_id="after-closed",
            event_type=BeforeToolCallPayload,
            priority=2,
            timeout_ms=100,
            fail_policy=FailPolicy.FAIL_CLOSED,
            scope=HookScope.GLOBAL,
            handler=after_closed,
        )
    )

    with pytest.raises(HookDispatchError):
        await fail_closed_bus.dispatch(payload)
    assert fail_closed_calls == []


@pytest.mark.asyncio
async def test_E_R06_string_fail_policy_is_coerced_and_fail_closed_enforced() -> None:
    bus = TypedEventBus()
    payload = BeforeToolCallPayload(
        tenant_id="tenant-a",
        execution_id="execution-a",
        trace_id="trace-a",
        tool_id="deploy",
        arguments={},
    )

    async def failing_hook(_payload: BeforeToolCallPayload) -> None:
        raise ValueError("boom")

    bus.register(
        HookRegistration(
            registration_id="string-closed",
            event_type=BeforeToolCallPayload,
            priority=1,
            timeout_ms=100,
            fail_policy=cast(FailPolicy, "fail_closed"),
            scope=cast(HookScope, "global"),
            handler=failing_hook,
        )
    )

    with pytest.raises(HookDispatchError):
        await bus.dispatch(payload)


@pytest.mark.asyncio
async def test_E_R06_sync_handler_timeout_is_enforced() -> None:
    bus = TypedEventBus()
    payload = BeforeToolCallPayload(
        tenant_id="tenant-a",
        execution_id="execution-a",
        trace_id="trace-a",
        tool_id="deploy",
        arguments={},
    )

    def blocking_hook(_payload: BeforeToolCallPayload) -> None:
        time.sleep(0.1)

    bus.register(
        HookRegistration(
            registration_id="sync-slow",
            event_type=BeforeToolCallPayload,
            priority=1,
            timeout_ms=1,
            fail_policy=FailPolicy.FAIL_CLOSED,
            scope=HookScope.GLOBAL,
            handler=blocking_hook,
        )
    )

    with pytest.raises(HookDispatchError):
        await bus.dispatch(payload)
