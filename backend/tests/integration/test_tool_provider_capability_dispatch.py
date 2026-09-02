"""ADR-EXT-001 TASK-003 验收测试：TOOL_EXECUTOR plugin 的 tool 经 Capability Contract
解析，dispatch 到真实 ToolRuntime。

S-02（integration，RULE-fluxion-workflow-001：Tool 是 Agent-facing Adapter，业务在 Capability）：

- 真实边界 1：`ToolProvider.capabilities()` → `CapabilityDescriptor`（真实 PluginLoader.load
  + 真实 TOOL_EXECUTOR plugin 实现 CapabilityProvider 协议，非 mock）。
- 真实边界 2：`ToolRuntime` dispatch（真实 ToolRuntime.register/call + 真实 _execute +
  真实 emit tool.policy_decision / tool.completed）。
- 桥接 adapter 为测试装配（reference binding）：把 LoadedPlugin.capabilities 的每个
  CapabilityDescriptor 映射成 ToolDescriptor(capability_id=descriptor.capability_id) +
  executor 委托回 plugin.execute。adapter 不是 S-02 声明的真实边界（声明边界只有
  capabilities()→descriptor 与 ToolRuntime dispatch 两端），故不构成 mock 绕过或层级降级。
- 产品级 plugin→ToolRuntime 接线（adapter 进产品 + PluginLoader 注入 Runtime）按 design
  §3.4 / 技术债(2) 延后 Phase 5 TASK-E501；本 ADR 只锁定分派契约形状并证明可 bind。

关键断言：
- capability_id 经 Capability Contract 解析：ToolDescriptor.capability_id == plugin
  CapabilityDescriptor.capability_id（非硬编码），trace tool.completed 携带同一 capability_id。
- plugin 是 Adapter：adapter 桥接 descriptor→ToolDescriptor+executor，plugin 经 capabilities()
  声明能力。
- 业务在 Capability（reference 占位）：tool 结果由 plugin.execute 产出。
- ToolRuntime dispatch：call 经 _execute + emit tool.policy_decision / tool.completed。

RED 约定（cf-task:start 规则 #7）：reference binding 验证——产品原语（PluginLoader.load +
LoadedPlugin.capabilities + ToolRuntime.register/call + CapabilityDescriptor）在 TASK-001/002
已落地，adapter 为测试装配，故 green-before；真实 RED 由 TASK-001 B-01（ToolProvider=
CapabilityProvider 契约形状定义）承载。不得伪造失败。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from fluxion.plugins.contracts import (
    CapabilityDescriptor,
    CapabilityProvider,
    PluginContext,
    PluginExecutionMode,
    PluginManifest,
    PluginType,
    ToolProvider,
    TrustLevel,
)
from fluxion.plugins.loader import PluginLoader
from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.tools import ToolDescriptor, ToolResultStatus, ToolRuntime
from tests.runtime_helpers import minimal_tool_context


def _manifest(plugin_id: str) -> PluginManifest:
    return PluginManifest(
        plugin_id=plugin_id,
        version="1",
        plugin_type=PluginType.TOOL_EXECUTOR,
        entrypoint=f"tests.{plugin_id}:Plugin",
        trust_level=TrustLevel.TRUSTED,
        permissions=[],
        dependencies=[],
        compatibility={"fluxion": ">=0.1"},
        execution_mode=PluginExecutionMode.IN_PROCESS,
    )


# ---- reference TOOL_EXECUTOR plugin（test double，实现 Protocol 真实方法）----


class _EchoToolPlugin:
    """reference TOOL_EXECUTOR plugin：经 capabilities() 声明一个 echo capability，
    execute() 承载业务（Capability 的 reference 占位；design 未定义独立可执行 Capability
    契约，完整 Capability 执行层分离归 Phase 5 TASK-E501）。"""

    manifest = _manifest("echo.tool")

    async def setup(self, ctx: PluginContext) -> None:
        del ctx

    async def shutdown(self) -> None:
        return None

    def capabilities(self) -> list[CapabilityDescriptor]:
        return [
            CapabilityDescriptor(
                capability_id="cap.echo",
                kind="tool",
                version="1",
                metadata={"name": "echo", "description": "echo back the text argument"},
            )
        ]

    async def execute(self, arguments: dict[str, object]) -> dict[str, object]:
        return {"echo": arguments.get("text", "")}


# ---- test-local reference binding adapter ----


def _bridge_tool_provider(loader: PluginLoader, runtime: ToolRuntime) -> None:
    """reference binding：LoadedPlugin.capabilities（CapabilityDescriptor）→
    ToolDescriptor(capability_id=descriptor.capability_id) + executor 委托 plugin.execute。

    证明 ADR-009 Tool=Adapter / Capability Contract 可 bind 且 ToolRuntime dispatch 跑通。
    产品级 adapter + PluginLoader→Runtime 注入按 design §3.4 延后 Phase 5 TASK-E501。
    """
    for loaded in loader.loaded:
        plugin = loader._loaded[loaded.manifest.plugin_id]
        execute: Callable[[dict[str, object]], Awaitable[dict[str, object]]] | None = (
            getattr(plugin, "execute", None)
        )
        if not callable(execute):
            continue
        for descriptor in loaded.capabilities:
            tool_id = str(descriptor.metadata.get("name") or descriptor.capability_id)

            async def _executor(
                _context: RuntimeContext,
                arguments: dict[str, object],
                _execute: Callable[[dict[str, object]], Awaitable[dict[str, object]]] = execute,
            ) -> dict[str, object]:
                return await _execute(arguments)

            runtime.register(
                ToolDescriptor(
                    tool_id=tool_id,
                    capability_id=descriptor.capability_id,
                    name=tool_id,
                    external_dependency=False,
                ),
                _executor,
            )


# ---- S-02 ----


@pytest.mark.asyncio
async def test_s02_tool_provider_dispatches_via_capability_contract() -> None:
    loader = PluginLoader()
    await loader.load(_EchoToolPlugin())

    # 真实边界 1：capabilities() → CapabilityDescriptor（plugin 真实实现 CapabilityProvider）
    plugin = _EchoToolPlugin()
    assert isinstance(plugin, CapabilityProvider)
    assert isinstance(plugin, ToolProvider)  # SPI-02：ToolProvider = CapabilityProvider
    loaded = loader.loaded[0]
    assert [c.capability_id for c in loaded.capabilities] == ["cap.echo"]

    tool_runtime = ToolRuntime()
    _bridge_tool_provider(loader, tool_runtime)

    # 经 Capability Contract 解析：ToolDescriptor.capability_id 来自 descriptor，非硬编码
    descriptor = tool_runtime.descriptor("echo")
    assert descriptor.capability_id == "cap.echo"
    assert descriptor.capability_id == loaded.capabilities[0].capability_id

    context = minimal_tool_context(
        {"user_tools": ["echo"], "agent_tools": ["echo"], "tenant_tools": ["echo"]}
    )

    # 真实边界 2：ToolRuntime dispatch（真实 call + _execute + emit）
    result = await tool_runtime.call(context, "echo", {"text": "hello"})

    assert result.status is ToolResultStatus.COMPLETED
    assert result.result == {"echo": "hello"}  # 业务在 Capability（plugin.execute）

    # trace 携带经 Capability Contract 解析的 capability_id（端到端一致）
    completed = [event for event in context.trace if event.name == "tool.completed"]
    assert completed
    assert completed[0].attributes["tool_id"] == "echo"
    assert completed[0].attributes["capability_id"] == "cap.echo"
    policy = [event for event in context.trace if event.name == "tool.policy_decision"]
    assert policy
    assert policy[0].attributes["capability_id"] == "cap.echo"
