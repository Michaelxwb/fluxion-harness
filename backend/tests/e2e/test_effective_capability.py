from __future__ import annotations

import pytest
from pydantic import ValidationError
from tests.runtime_helpers import publish_resource

from fluxion.registry import RegistryStore
from fluxion.resources import ResourceBinding, ResourceKind, SubjectType
from fluxion.runtime.capabilities import EffectiveCapabilityResolver


async def _bind_tenant_policy(store: RegistryStore, resource_id: str, selector: str = "latest-published") -> None:
    await store.put_binding(
        ResourceBinding(
            binding_id=f"binding-{resource_id}",
            tenant_id="tenant-a",
            subject_type=SubjectType.TENANT,
            subject_id="tenant-a",
            resource_type=ResourceKind.POLICY,
            resource_id=resource_id,
            resource_version_selector=selector,
        )
    )


@pytest.mark.asyncio
async def test_RS4_tenant_policy_allow_list_mode(sqlite_store: RegistryStore) -> None:
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.POLICY,
        resource_id="allow-policy",
        version="1",
        spec={
            "name": "allow",
            "allowed_tools": ["mcp__weather__current", "mcp__weather__audit"],
        },
    )
    await _bind_tenant_policy(sqlite_store, "allow-policy")

    resolver = EffectiveCapabilityResolver(sqlite_store)
    allowed, denied, configured = await resolver.tenant_policy_tools(tenant_id="tenant-a")

    assert configured is True
    assert allowed == {"mcp__weather__current", "mcp__weather__audit"}
    assert denied == set()


@pytest.mark.asyncio
async def test_RS4_tenant_policy_deny_only_mode(sqlite_store: RegistryStore) -> None:
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.POLICY,
        resource_id="deny-policy",
        version="1",
        spec={"name": "deny", "denied_tools": ["mcp__weather__delete"]},
    )
    await _bind_tenant_policy(sqlite_store, "deny-policy")

    resolver = EffectiveCapabilityResolver(sqlite_store)
    allowed, denied, configured = await resolver.tenant_policy_tools(tenant_id="tenant-a")

    # deny-only（allowed 为空）：调用方不缩小集合，仅从各维度移除 denied
    assert configured is True
    assert allowed == set()
    assert denied == {"mcp__weather__delete"}


@pytest.mark.asyncio
async def test_RS4_no_policy_binding_leaves_unconfigured(sqlite_store: RegistryStore) -> None:
    resolver = EffectiveCapabilityResolver(sqlite_store)
    allowed, denied, configured = await resolver.tenant_policy_tools(tenant_id="tenant-a")

    assert (allowed, denied, configured) == (set(), set(), False)


@pytest.mark.asyncio
async def test_RS4_multiple_policy_bindings_merge(sqlite_store: RegistryStore) -> None:
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.POLICY,
        resource_id="policy-a",
        version="1",
        spec={"name": "a", "allowed_tools": ["tool-1"], "denied_tools": ["tool-9"]},
    )
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.POLICY,
        resource_id="policy-b",
        version="1",
        spec={"name": "b", "allowed_tools": ["tool-2"], "denied_tools": ["tool-8"]},
    )
    await _bind_tenant_policy(sqlite_store, "policy-a")
    await _bind_tenant_policy(sqlite_store, "policy-b")

    resolver = EffectiveCapabilityResolver(sqlite_store)
    allowed, denied, configured = await resolver.tenant_policy_tools(tenant_id="tenant-a")

    assert configured is True
    assert allowed == {"tool-1", "tool-2"}
    assert denied == {"tool-8", "tool-9"}


@pytest.mark.asyncio
async def test_RS4_policy_spec_with_removed_field_rejected(sqlite_store: RegistryStore) -> None:
    # ADR-012：PolicyDefinition 无 rules 字段；旧 spec（校验/运行时键漂移的
    # 根因）在读取端即被 model_validate 拒绝，不再静默忽略。
    await publish_resource(
        sqlite_store,
        tenant_id="tenant-a",
        kind=ResourceKind.POLICY,
        resource_id="legacy-policy",
        version="1",
        spec={"name": "legacy", "rules": []},
    )
    await _bind_tenant_policy(sqlite_store, "legacy-policy")

    resolver = EffectiveCapabilityResolver(sqlite_store)
    with pytest.raises(ValidationError):
        await resolver.tenant_policy_tools(tenant_id="tenant-a")


@pytest.mark.asyncio
async def test_RS4_pinned_draft_policy_rejected(sqlite_store: RegistryStore) -> None:
    # put_binding 是裸 insert，不校验资源状态；显式 pin 到 DRAFT 版本的
    # binding 可达，_required_resource 的 PUBLISHED 检查负责把它挡在授权计算外
    # （与 ResourceResolver 的 PUBLISHED 校验对齐）。
    from tests.runtime_helpers import resource_definition

    await sqlite_store.put(
        resource_definition(
            tenant_id="tenant-a",
            kind=ResourceKind.POLICY,
            resource_id="draft-policy",
            version="1",
            spec={"name": "draft", "allowed_tools": ["tool-1"]},
        )
    )
    await _bind_tenant_policy(sqlite_store, "draft-policy", selector="1")

    resolver = EffectiveCapabilityResolver(sqlite_store)
    with pytest.raises(LookupError, match="is not published"):
        await resolver.tenant_policy_tools(tenant_id="tenant-a")
