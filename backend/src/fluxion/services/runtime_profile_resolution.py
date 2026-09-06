"""ADR-A010（golden-path-closure TASK-002）：RuntimeProfile 默认解析链。

`Agent.runtime_profile_ref` 未配置时的唯一解析路径：

    Tenant Default RuntimeProfile（同租户 default=true 的 published 资源）
        ↓（不存在时）
    platform-default（系统内置资源，bootstrap 保证存在）

Agent ID == RuntimeProfile ID 的同名回退已废弃；本模块供 ContextResolver 与
执行入口（agents_app / channel_app / studio test-run）共用，禁止各处再自行回退。
"""

from __future__ import annotations

from fluxion.registry.store import RegistryStore
from fluxion.resources import ResourceKind
from fluxion.resources.contract_base import ResourceStatus
from fluxion.resources.contracts import ResourceDefinition
from fluxion.resources.resource_specs import RuntimeProfile

PLATFORM_DEFAULT_PROFILE_ID = "platform-default"

_PAGE_SIZE = 100


async def resolve_default_runtime_profile(
    store: RegistryStore, tenant_id: str
) -> ResourceDefinition | None:
    """租户默认 → platform-default；两者皆无返回 None（调用方 fail-closed）。

    只认当前 published 版本的 default=true；draft 默认不生效，版本更替后
    default 不自动延续（新版本须显式保留 default=true，与 Published Resource
    不可原地修改一致）。写入侧（publish 治理）拒绝同租户跨资源多 default
    并存；list_resources 返回当前版本集，扫描命中即返回。
    """
    offset = 0
    while True:
        rows, total = await store.list_resources(
            ResourceKind.RUNTIME_PROFILE, tenant_id=tenant_id, offset=offset, limit=_PAGE_SIZE
        )
        for row in rows:
            if row.status is not ResourceStatus.PUBLISHED:
                continue
            if RuntimeProfile.model_validate(row.spec_json).default:
                return row
        offset += _PAGE_SIZE
        if offset >= total:
            break
    # store.get 无 version → latest published（draft-only 资源不会被选中）
    return await store.get(
        ResourceKind.RUNTIME_PROFILE,
        PLATFORM_DEFAULT_PROFILE_ID,
        tenant_id=tenant_id,
    )
