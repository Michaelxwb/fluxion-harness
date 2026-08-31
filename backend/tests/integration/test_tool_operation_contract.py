from __future__ import annotations

import pytest

from fluxion.runtime.tools import (
    IdempotencySpec,
    ToolDescriptor,
    ToolResultStatus,
    ToolRuntime,
)
from tests.runtime_helpers import minimal_tool_context


@pytest.mark.asyncio
async def test_S03_idempotency_key_replay_does_not_repeat_side_effect() -> None:
    """S-03：command + side_effect + idempotency 声明，同幂等键重放不重复副作用。"""
    calls: list[str] = []

    def execute(_ctx: object, args: dict[str, object]) -> dict[str, object]:
        calls.append(str(args["id"]))
        return {"created": str(args["id"])}

    runtime = ToolRuntime()
    runtime.register(
        ToolDescriptor(
            tool_id="orders.create",
            capability_id="cap.orders",
            name="orders.create",
            operation="command",
            side_effect=True,
            idempotency=IdempotencySpec(key_field="id"),
        ),
        execute,
    )
    context = minimal_tool_context(
        {
            "user_tools": ["orders.create"],
            "agent_tools": ["orders.create"],
            "tenant_tools": ["orders.create"],
        }
    )

    first = await runtime.call(context, "orders.create", {"id": "o-1", "amount": 10})
    replay = await runtime.call(context, "orders.create", {"id": "o-1", "amount": 10})
    other = await runtime.call(context, "orders.create", {"id": "o-2", "amount": 20})

    # 副作用只执行两次：o-1 首次 + o-2；o-1 第二次命中幂等缓存不重复执行。
    assert calls == ["o-1", "o-2"]
    assert first.status is ToolResultStatus.COMPLETED
    assert replay.result == first.result == {"created": "o-1"}
    # 重放返回首次结果（policy_decision_id 一致，未重新生成）。
    assert replay.policy_decision_id == first.policy_decision_id
    assert other.result == {"created": "o-2"}
    # 重放可观测。
    assert any(event.name == "tool.idempotent_replay" for event in context.trace)
