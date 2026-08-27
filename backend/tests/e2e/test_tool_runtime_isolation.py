from __future__ import annotations

import pytest
from tests.runtime_helpers import publish_resource

from fluxion.plugins.model_provider import ModelProviderRegistry
from fluxion.resources import ResourceKind
from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.tools import ToolDescriptor, ToolNotFoundError, ToolRuntime
from fluxion.services.runtime_app import RuntimeApplicationService
from fluxion.services.runtime_contracts import RunRuntimeRequest
from fluxion.services.runtime_utils import DevEchoModelProvider


def test_S_F4_clone_isolates_mcp_descriptors_from_base() -> None:
    """F4 单元：clone_for_execution 拷贝 base 的 builtin/注入工具（共享引用），
    往 clone 注册 MCP descriptor（含 credential_ref）不污染 base，两 clone 互不
    可见——per-execution 隔离原语。descriptor 是 frozen dataclass、executor 是
    无状态 callable，共享引用安全。"""
    base = ToolRuntime()
    base.register(
        ToolDescriptor(tool_id="builtin.x", capability_id="cap.x", name="x"),
        lambda _ctx, _args: {"ok": True},
    )
    clone_a = base.clone_for_execution()
    clone_b = base.clone_for_execution()
    # base 的 builtin 被拷入两个 clone（同一 frozen descriptor 引用）
    assert clone_a.descriptor("builtin.x") is base.descriptor("builtin.x")
    assert clone_b.descriptor("builtin.x") is base.descriptor("builtin.x")
    # 往 clone_a 注册 MCP（含 credential_ref）不污染 base / clone_b
    clone_a.register(
        ToolDescriptor(
            tool_id="mcp__github__list_pr",
            capability_id="mcp.github.list_pr",
            name="list_pr",
            credential_ref="secret://tenant-a/github-token",
        ),
        lambda _ctx, _args: {"pr": "ok"},
    )
    for runtime in (base, clone_b):
        with pytest.raises(ToolNotFoundError):
            runtime.descriptor("mcp__github__list_pr")
    assert (
        clone_a.descriptor("mcp__github__list_pr").credential_ref
        == "secret://tenant-a/github-token"
    )


class _RecordingMCPRuntime:
    """F4 测试桩：记录每次 prepare 收到的 tool_runtime（即 per-execution clone），
    并按当前租户注册同 tool_id、不同 credential_ref 的 MCP descriptor——模拟两
    租户各自绑定同一 MCP 的真实场景。替代真实 RegistryMCPRuntime 以隔离单测。"""

    def __init__(self) -> None:
        self.seen: list[ToolRuntime] = []

    async def prepare(
        self, context: RuntimeContext, tool_runtime: ToolRuntime
    ) -> set[str]:
        self.seen.append(tool_runtime)
        tenant = context.snapshot.tenant_id
        tool_runtime.register(
            ToolDescriptor(
                tool_id="mcp__github__list_pr",
                capability_id="mcp.github.list_pr",
                name="list_pr",
                credential_ref=f"secret://{tenant}/github-token",
            ),
            lambda _ctx, _args: {"pr": "ok"},
        )
        return {"mcp__github__list_pr"}

    async def close(self) -> None:
        return None


async def test_S_F4_mcp_descriptors_isolated_per_execution_and_not_accumulated(
    sqlite_store,  # type: ignore[no-untyped-def]  # fixture: RegistryStore
) -> None:
    """F4 行为：ToolRuntime 原为进程级单例跨租户共享，prepare 累积注册 MCP
    descriptor（含 credential_ref）、无 unregister → 跨租户泄漏 + 无界增长 +
    disable 后 stale。per-execution clone 后：每次 run clone 自 base、MCP 注入
    副本；base 不被污染；两租户的同 tool_id MCP descriptor（异 credential_ref）
    互不可见、各驻各自 clone。"""
    for tenant in ("tenant-a", "tenant-b"):
        await publish_resource(
            sqlite_store,
            tenant_id=tenant,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="1",
            spec={
                "request_timeout_ms": 2_000,
                "max_retries": 1,
                "max_rounds": 1,
            },
        )
        # TASK-A104：persona/model 迁至同名 AgentDefinition（两租户各一份）。
        await publish_resource(
            sqlite_store,
            tenant_id=tenant,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="assistant",
            version="1",
            spec={
                "name": "assistant",
                "system_prompt": "echo",
                "owner": "fixture",
                "model_ref": {"id": "dev.echo", "version": "1"},
            },
        )
    model_registry = ModelProviderRegistry()
    model_registry.register("dev.echo", DevEchoModelProvider())
    base = ToolRuntime()
    stub = _RecordingMCPRuntime()
    service = RuntimeApplicationService(
        sqlite_store,
        model_providers=model_registry,
        tool_runtime=base,
        mcp_runtime=stub,
    )

    for tenant in ("tenant-a", "tenant-b"):
        await service.run(
            RunRuntimeRequest(
                tenant_id=tenant,
                user_id=f"{tenant}-user",
                runtime_profile_id="assistant",
                session_id=f"{tenant}-session",
                input_message="hi",
            )
        )

    # 两次 prepare 收到的是不同 clone（per-execution 隔离）
    assert len(stub.seen) == 2
    assert stub.seen[0] is not stub.seen[1]
    # base 未被任何 MCP descriptor 污染（无累积 / 无跨租户泄漏）
    with pytest.raises(ToolNotFoundError):
        base.descriptor("mcp__github__list_pr")
    # 两租户的 credential_ref 各自隔离在自己的 clone 里，互不可见
    assert (
        stub.seen[0].descriptor("mcp__github__list_pr").credential_ref
        == "secret://tenant-a/github-token"
    )
    assert (
        stub.seen[1].descriptor("mcp__github__list_pr").credential_ref
        == "secret://tenant-b/github-token"
    )
