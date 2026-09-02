"""REQ-CAP-002/003（TASK-003）：Tool/MCP 有效权限 = UserGrant ∩ AgentAllowlist ∩ TenantPolicy。

- B-S-02：三维交集正确（缺任一维度即不可用）。
- B-E-01：仅用户 grant、agent allowlist 为空 → fail-closed。

单一 EffectiveCapability Resolver 不变量：`context_resolver` 在快照期冻结
`effective_permissions`（user/agent/tenant 三元组 + policy 模式），运行期
`frozen_tool_policy` 强制执行，不另起第二套授权逻辑。前两个用例从冻结三元组
直接驱动运行期强制（机制层）；B-S-02 真实边界用例走 Grant Store →
ContextResolver → ToolRuntime 全链（TASK-003 返工：不再只手工构造冻结集合）。
"""

from __future__ import annotations

import pytest

from fluxion.registry import RegistryStore
from fluxion.resources import ResourceKind
from fluxion.runtime.builtin_tools import BuiltinToolConfig, register_builtin_tools
from fluxion.runtime.context import RequestContext, RuntimeContext
from fluxion.runtime.tools import (
    ToolAuthorizationError,
    ToolResultStatus,
    ToolRuntime,
)
from fluxion.services.context_resolver import ContextResolver, ResolverSelector
from tests.runtime_helpers import (
    minimal_tool_context,
    publish_resource,
    seed_model_definition,
    seed_tenant_policy,
)


def _tool_runtime() -> ToolRuntime:
    runtime = ToolRuntime()
    register_builtin_tools(runtime, BuiltinToolConfig())
    return runtime


@pytest.mark.asyncio
async def test_B_S02_effective_tool_is_three_way_intersection() -> None:
    """B-S-02：有效工具 = user ∩ agent ∩ tenant 交集（REQ-CAP-002）。"""
    runtime = _tool_runtime()
    context = minimal_tool_context(
        {
            "user_tools": ["time.now", "calc.eval", "http.get"],
            "agent_tools": ["time.now", "calc.eval"],
            "tenant_tools": ["time.now"],
        }
    )
    # 只有三个维度都包含的 time.now 可执行
    descriptors = runtime.list_effective_descriptors(context)
    assert {d.tool_id for d in descriptors} == {"time.now"}

    ok = await runtime.call(context, "time.now", {})
    assert ok.status is ToolResultStatus.COMPLETED

    # user/agent 有但 tenant 没有 → fail-closed
    with pytest.raises(ToolAuthorizationError) as calc_err:
        await runtime.call(context, "calc.eval", {})
    assert calc_err.value.code == "tool_not_allowed"
    # user 有但 agent 没有 → fail-closed
    with pytest.raises(ToolAuthorizationError):
        await runtime.call(context, "http.get", {})


@pytest.mark.asyncio
async def test_B_E01_user_grant_without_agent_allowlist_fails_closed() -> None:
    """B-E-01：仅用户 grant、agent allowlist 为空 → fail-closed（REQ-CAP-003）。"""
    runtime = _tool_runtime()
    context = minimal_tool_context(
        {
            "user_tools": ["time.now"],
            "agent_tools": [],
            "tenant_tools": ["time.now"],
        }
    )
    with pytest.raises(ToolAuthorizationError) as err:
        await runtime.call(context, "time.now", {})
    assert err.value.code == "tool_not_allowed"


@pytest.mark.asyncio
async def test_B_S02_real_chain_grant_store_to_runtime(
    sqlite_store: RegistryStore,
) -> None:
    """B-S-02（TASK-003 返工）：真实边界 Grant Store → ContextResolver →
    ToolRuntime 三重交集。

    - tenant-a：三维齐备（grant + agent 声明 + tenant allow-list）→ 工具可调用；
    - tenant-b：无 tenant policy → tenant 维度空集，fail-closed（design/02 §3
      真值表 row 3），不再拷贝 user_tools 到 tenant 维度。
    """
    capabilities = [
        {"capability_ref": tool, "version_pin": "1", "type": "tool"}
        for tool in ("time.now", "calc.eval")
    ]
    for tenant_id, allowed in (("tenant-a", ["time.now", "calc.eval"]), ("tenant-b", None)):
        await publish_resource(
            sqlite_store,
            tenant_id=tenant_id,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="1",
            spec={"request_timeout_ms": 30_000, "max_retries": 1},
        )
        await seed_model_definition(sqlite_store, tenant_id=tenant_id, provider_id="dev.echo")
        await publish_resource(
            sqlite_store,
            tenant_id=tenant_id,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="assistant",
            version="1",
            spec={
                "name": "assistant",
                "system_prompt": "p",
                "owner": "builder",
                "model_policy": {
                    "primary_model_ref": {"id": "model.dev.echo", "version": "1"}
                },
                "capabilities": capabilities,
            },
        )
        for tool in ("time.now", "calc.eval"):
            await sqlite_store.add_capability_grant(
                tenant_id=tenant_id,
                platform_user_id="user-a",
                capability_ref=tool,
                granted_scope="invoke",
                version_pin="1",
                capability_kind="tool",
            )
        if allowed is not None:
            await seed_tenant_policy(
                sqlite_store, tenant_id=tenant_id, allowed_tools=allowed
            )

    resolver = ContextResolver(sqlite_store)
    runtime = _tool_runtime()

    # tenant-a：三维齐备 → 快照冻结三元组 + policy 模式，工具可调用
    result_a = await resolver.resolve(
        ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a"),
        session_id="s-a",
    )
    perms_a = result_a.snapshot.effective_permissions
    assert perms_a["user_tools"] == ["calc.eval", "time.now"]
    assert perms_a["agent_tools"] == ["calc.eval", "time.now"]
    assert perms_a["tenant_tools"] == ["calc.eval", "time.now"]
    assert perms_a["tenant_tool_policy"] == "allow_list"
    context_a = RuntimeContext(
        request=RequestContext(tenant_id="tenant-a", user_id="user-a", session_id="s-a"),
        snapshot=result_a.snapshot,
    )
    ok = await runtime.call(context_a, "time.now", {})
    assert ok.status is ToolResultStatus.COMPLETED

    # tenant-b：无 tenant policy → tenant 维度空集（fail-closed），调用被拒
    result_b = await resolver.resolve(
        ResolverSelector(tenant_id="tenant-b", agent_id="assistant", user_id="user-a"),
        session_id="s-b",
    )
    perms_b = result_b.snapshot.effective_permissions
    assert perms_b["user_tools"] == ["calc.eval", "time.now"]
    assert perms_b["tenant_tools"] == []
    assert perms_b["tenant_tool_policy"] == "unconfigured"
    context_b = RuntimeContext(
        request=RequestContext(tenant_id="tenant-b", user_id="user-a", session_id="s-b"),
        snapshot=result_b.snapshot,
    )
    with pytest.raises(ToolAuthorizationError) as denied:
        await runtime.call(context_b, "time.now", {})
    assert denied.value.code == "tool_not_allowed"


@pytest.mark.asyncio
async def test_B_S02_deny_only_policy_allows_unless_denied(
    sqlite_store: RegistryStore,
) -> None:
    """deny-only 模式（allowed 为空）：除 denied 外全部放行——tenant 维度不设
    allow-list；denied 始终优先（含被 deny 的工具不可调用）。"""
    capabilities = [
        {"capability_ref": tool, "version_pin": "1", "type": "tool"}
        for tool in ("time.now", "calc.eval")
    ]
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"request_timeout_ms": 30_000, "max_retries": 1},
    )
    await seed_model_definition(sqlite_store, tenant_id="tenant-a", provider_id="dev.echo")
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.AGENT_DEFINITION,
        resource_id="assistant",
        version="1",
        spec={
            "name": "assistant",
            "system_prompt": "p",
            "owner": "builder",
            "model_policy": {"primary_model_ref": {"id": "model.dev.echo", "version": "1"}},
            "capabilities": capabilities,
        },
    )
    for tool in ("time.now", "calc.eval"):
        await sqlite_store.add_capability_grant(
            tenant_id="tenant-a",
            platform_user_id="user-a",
            capability_ref=tool,
            granted_scope="invoke",
            version_pin="1",
            capability_kind="tool",
        )
    await seed_tenant_policy(
        sqlite_store, tenant_id="tenant-a", denied_tools=["calc.eval"]
    )

    result = await ContextResolver(sqlite_store).resolve(
        ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a"),
        session_id="s-a",
    )
    perms = result.snapshot.effective_permissions
    # deny-only：tenant 冻结图为空集（运行期按「除 denied 外全部」展开），
    # denied 已从 user/agent 维度移除
    assert perms["tenant_tool_policy"] == "deny_only"
    assert perms["tenant_tools"] == []
    assert perms["denied_tools"] == ["calc.eval"]
    assert "calc.eval" not in perms["user_tools"]
    assert "time.now" in perms["user_tools"]

    context = RuntimeContext(
        request=RequestContext(tenant_id="tenant-a", user_id="user-a", session_id="s-a"),
        snapshot=result.snapshot,
    )
    runtime = _tool_runtime()
    ok = await runtime.call(context, "time.now", {})
    assert ok.status is ToolResultStatus.COMPLETED
    with pytest.raises(ToolAuthorizationError):
        await runtime.call(context, "calc.eval", {})
