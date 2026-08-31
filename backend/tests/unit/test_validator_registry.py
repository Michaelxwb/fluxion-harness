from __future__ import annotations

import pytest

from fluxion.runtime.tools import (
    PolicyDecision,
    ToolAuthorizationError,
    ToolDescriptor,
    ToolRuntime,
    ValidatorRegistry,
)
from tests.runtime_helpers import minimal_tool_context


class _DenyValidator:
    async def validate(self, context, descriptor, arguments) -> PolicyDecision:
        return PolicyDecision.DENY


def _context(tool_id: str):
    return minimal_tool_context(
        {"user_tools": [tool_id], "agent_tools": [tool_id], "tenant_tools": [tool_id]}
    )


def _runtime(tool_id: str, registry: ValidatorRegistry | None = None) -> ToolRuntime:
    runtime = ToolRuntime(semantic_validators=registry)
    runtime.register(
        ToolDescriptor(tool_id=tool_id, capability_id="cap.x", name=tool_id),
        lambda ctx, args: {"ok": True},
    )
    return runtime


def test_T004_validator_registry_register_dedupes_and_snapshots() -> None:
    v = _DenyValidator()
    registry = ValidatorRegistry(version="3")
    registry.register(v)
    registry.register(v)  # 幂等：重复注册不叠加
    assert registry.version == "3"
    assert registry.snapshot() == (v,)


@pytest.mark.asyncio
async def test_T004_tool_runtime_uses_injected_registry() -> None:
    registry = ValidatorRegistry()
    registry.register(_DenyValidator())
    runtime = _runtime("t", registry)

    with pytest.raises(ToolAuthorizationError) as exc:
        await runtime.call(_context("t"), "t", {})
    assert exc.value.code == "semantic_invalid"


@pytest.mark.asyncio
async def test_T004_no_global_leak_between_runtimes() -> None:
    # 去 process-global 可变：runtime_a 注入 DENY validator，runtime_b 无注入，
    # 两者互不泄漏（无全局 list 累积）。
    denying = ValidatorRegistry()
    denying.register(_DenyValidator())
    runtime_a = _runtime("t", denying)
    runtime_b = _runtime("t")  # 默认空 registry

    with pytest.raises(ToolAuthorizationError):
        await runtime_a.call(_context("t"), "t", {})

    result = await runtime_b.call(_context("t"), "t", {})
    assert result.result == {"ok": True}
