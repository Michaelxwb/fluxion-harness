from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import cast

import pytest

from fluxion.kernel.events import (
    BeforeToolCallPayload,
    FailPolicy,
    HookDispatchError,
    HookRegistration,
    TypedEventBus,
)
from fluxion.resources import ExecutionSnapshot
from fluxion.runtime import RequestContext, RuntimeContext
from tests.runtime_helpers import hook_run_request, hook_test_service

# 105 P1-02（TASK-006）：S-04/E-02 服务级验收用真实示例插件
#（backend/examples/audit_hook），经 composition root 真实安装路径。
_EXAMPLES_DIR = str(Path(__file__).resolve().parents[2] / "examples")
if _EXAMPLES_DIR not in sys.path:
    sys.path.insert(0, _EXAMPLES_DIR)


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
        model_resolution={
            "routes": [
                {
                    "provider_ref": {"id": "stub", "version": "1"},
                    "model_ref": {"id": "model-stub", "version": "1"},
                    "model": "stub",
                }
            ]
        },
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
            handler=failing_hook,
        )
    )

    with pytest.raises(HookDispatchError):
        await bus.dispatch(payload)


@pytest.mark.asyncio
async def test_E_R06_sync_handler_is_rejected() -> None:
    """E-R06（TASK-006 新契约）：sync handler 注册期被拒绝（async-only）。

    旧语义（to_thread＋wait_for 约束执行时间）已删除：timeout 只能停止等待、
    不能终止后台线程，注释与实现一并收敛。
    """

    def blocking_hook(_payload: BeforeToolCallPayload) -> None:
        time.sleep(0.1)

    with pytest.raises(ValueError, match="async"):
        HookRegistration(
            registration_id="sync-slow",
            event_type=BeforeToolCallPayload,
            priority=1,
            timeout_ms=1,
            fail_policy=FailPolicy.FAIL_CLOSED,
            handler=blocking_hook,
        )


# --- 105 P1-02（TASK-006）：S-04/E-02 服务级最终验收 ---
# harness 已提升至 tests.runtime_helpers（hook_test_service/hook_run_request），
# 本文件直接使用共享实现。


@pytest.mark.asyncio
async def test_S_04_all_eight_hook_points_fire_via_service_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-04（最终验收）：真实 service＋entry_points fixture 包，8 点位各至少触发一次。

    成功执行覆盖 6 点；失败执行覆盖 on_error；取消执行覆盖 on_cancelled。
    tool 调用前后各一条 audit 记录落插件；hook.completed 进 trace（audit 落点）。
    """
    from audit_hook import AuditHookPlugin

    plugin = AuditHookPlugin()
    monkeypatch.setattr(
        "fluxion.plugins.loader.discover_hook_plugins", lambda: [plugin]
    )
    service, _store = await hook_test_service()
    try:
        result = await service.run(hook_run_request())
        assert result.runtime_profile_version == "1"

        points = {record.point for record in plugin.records}
        assert {
            "before_execution",
            "before_model",
            "after_model",
            "before_tool",
            "after_tool",
            "after_execution",
        } <= points
        tool_points = [r for r in plugin.records if r.point in {"before_tool", "after_tool"}]
        assert any(r.summary == "tool=time.now" for r in tool_points)
        assert any(r.summary == "tool=time.now status=completed" for r in tool_points)

        trace = await service.trace_store.get(result.trace_id)
        assert trace is not None
        assert trace.hooks, "hook 执行须经 trace_sink 落点"

        # 失败执行 → on_error（有上下文的业务失败：已存在但未授权的工具）。
        from fluxion.services.runtime_app import RuntimeApplicationError, ToolCallRequest

        with pytest.raises(RuntimeApplicationError):
            await service.run(
                hook_run_request(
                    session_id="session-error",
                    tool_calls=[ToolCallRequest(tool_id="calc.eval", arguments={})],
                )
            )
        assert any(r.point == "on_error" for r in plugin.records)

        # 取消执行 → on_cancelled（FAIL_CLOSED 取消钩子阻断模型调用）。
        from fluxion.kernel.events import BeforeModelCallPayload

        async def _cancel(_payload: BeforeModelCallPayload) -> None:
            raise asyncio.CancelledError

        service._event_bus.register(
            HookRegistration(
                registration_id="cancel-hook",
                event_type=BeforeModelCallPayload,
                priority=1,
                timeout_ms=100,
                fail_policy=FailPolicy.FAIL_CLOSED,
                handler=_cancel,
            )
        )
        with pytest.raises(asyncio.CancelledError):
            await service.run(hook_run_request(session_id="session-cancel"))
        assert any(r.point == "on_cancelled" for r in plugin.records)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_E_02_service_fail_policy_blocks_or_continues() -> None:
    """E-02（最终验收）：FAIL_CLOSED 阻断业务；FAIL_OPEN 业务继续＋异常记录。"""
    from fluxion.kernel.events import BeforeToolCallPayload

    async def _boom(_payload: BeforeToolCallPayload) -> None:
        raise ValueError("hook-boom")

    # FAIL_CLOSED：业务被阻断。
    closed_bus = TypedEventBus()
    closed_bus.register(
        HookRegistration(
            registration_id="closed-boom",
            event_type=BeforeToolCallPayload,
            priority=1,
            timeout_ms=100,
            fail_policy=FailPolicy.FAIL_CLOSED,
            handler=_boom,
        )
    )
    service, _store = await hook_test_service(closed_bus)
    try:
        # FAIL_CLOSED 阻断业务：HookDispatchError 经服务层规范包装为
        # RuntimeApplicationError（hook_dispatch_failed）上抛。
        from fluxion.services.runtime_app import RuntimeApplicationError

        with pytest.raises(RuntimeApplicationError) as exc_info:
            await service.run(hook_run_request())
        assert exc_info.value.code == "hook_dispatch_failed"
    finally:
        await service.close()

    # FAIL_OPEN：业务继续＋hook.error 记录（带执行关联 ID）。
    open_bus = TypedEventBus()
    open_bus.register(
        HookRegistration(
            registration_id="open-boom",
            event_type=BeforeToolCallPayload,
            priority=1,
            timeout_ms=100,
            fail_policy=FailPolicy.FAIL_OPEN,
            handler=_boom,
        )
    )
    service2, _store2 = await hook_test_service(open_bus)
    try:
        result = await service2.run(hook_run_request())
        trace = await service2.trace_store.get(result.trace_id)
        assert trace is not None and trace.error is None
        hook_errors = [e for e in trace.events if e.name == "hook.error"]
        assert any(
            e.attributes.get("registration_id") == "open-boom" for e in hook_errors
        )
    finally:
        await service2.close()
