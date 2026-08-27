"""ADR-EXT-001 TASK-004 验收测试：Plugin 作为 versioned Resource 发布 + SecretRef credential 路径。

S-03（integration，RULE-fluxion-resource-001 + RULE-EXT-04）：

- 真实边界 1：`resource_definitions` 行——发布 Plugin Resource（kind=plugin）经
  真实 `SQLiteRegistryStore.put` + `publish`，落真实 `resource_definitions` 表行
  （kind=plugin, version, status=published, spec_json=PluginManifest spec）。
- 真实边界 2：`resource_bindings.credential_ref`——绑定 SECRET_PROVIDER credential
  经真实 `SQLiteRegistryStore.put_binding`，落真实 `resource_bindings` 表行
  （resource_type=plugin, credential_ref=secret:// SecretRef）。
- spec_json 无明文 secret（RULE-EXT-04：credential 不入 spec）：
  `ResourceDefinition.validate_definition` 的 `assert_no_plaintext_secret` 拒绝明文
  secret；`ResourceBinding.validate_binding` 强制 credential_ref 用 secret://。

RED 约定（cf-task:start #7）：已有行为补测——产品原语（`ResourceKind.PLUGIN` +
`ResourceBinding.credential_ref` + secret:// validator + `assert_no_plaintext_secret`
+ `SQLiteRegistryStore.put/publish/put_binding`）在 RS 阶段已落地，S-03 集成验证为
green-before；真实 RED 由 RS 阶段契约定义承载（`ResourceKind.PLUGIN` enum 加入 +
credential_ref + secret:// validator + `assert_no_plaintext_secret` 落地时的 RED）。
不得伪造失败。
"""

from __future__ import annotations

import pytest

from fluxion.registry import SQLiteRegistryStore
from fluxion.resources import (
    ResourceBinding,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
    SubjectType,
)
from tests.runtime_helpers import publish_resource


@pytest.mark.asyncio
async def test_s03_plugin_resource_publish_and_credential_binding() -> None:
    # 真实边界：SQLiteRegistryStore（sqlite+aiosqlite:///:memory:），非 mock。
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        # PluginManifest spec——无明文 secret（RULE-EXT-04：credential 不入 spec）。
        plugin_spec: dict[str, object] = {
            "plugin_type": "secret_provider",
            "entrypoint": "fluxion.plugins.secrets.example:EnvSecretProvider",
            "trust_level": "trusted",
        }

        # 真实边界 1：发布 Plugin Resource（kind=plugin, 版本化）→ resource_definitions 行
        published = await publish_resource(
            store,
            tenant_id="tenant-a",
            kind=ResourceKind.PLUGIN,
            resource_id="secret-provider",
            version="1",
            spec=plugin_spec,
        )
        assert published.kind == ResourceKind.PLUGIN
        assert published.status == ResourceStatus.PUBLISHED
        assert published.version == "1"
        assert published.spec_json == plugin_spec

        # 行级回读：真实 resource_definitions 表行（kind=plugin, status=published, version）
        fetched = await store.get(
            ResourceKind.PLUGIN,
            "secret-provider",
            tenant_id="tenant-a",
        )
        assert fetched is not None
        assert fetched.kind == ResourceKind.PLUGIN
        assert fetched.status == ResourceStatus.PUBLISHED
        assert fetched.version == "1"
        assert fetched.spec_json == plugin_spec

        # 真实边界 2：绑定 SECRET_PROVIDER credential → resource_bindings.credential_ref（SecretRef）
        binding = ResourceBinding(
            binding_id="binding-tenant-a-user-a-secret-provider",
            tenant_id="tenant-a",
            subject_type=SubjectType.USER,
            subject_id="user-a",
            resource_type=ResourceKind.PLUGIN,
            resource_id="secret-provider",
            resource_version_selector="latest-published",
            credential_ref="secret://tenant-a/users/user-a/secret-provider",
            enabled=True,
        )
        await store.put_binding(binding)

        bindings = await store.list_bindings(
            subject_type="user",
            subject_id="user-a",
            tenant_id="tenant-a",
            resource_type=ResourceKind.PLUGIN,
        )
        assert len(bindings) == 1
        bound = bindings[0]
        assert bound.resource_type == ResourceKind.PLUGIN
        assert bound.resource_id == "secret-provider"
        # credential 走 resource_bindings.credential_ref（secret:// SecretRef），非 spec
        assert bound.credential_ref == "secret://tenant-a/users/user-a/secret-provider"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_s03_spec_json_rejects_plaintext_secret() -> None:
    # RULE-EXT-04：credential 不入 spec——ResourceDefinition.validate_definition
    # 的 assert_no_plaintext_secret 拒绝明文 secret（secret:// 引用值才豁免）。
    with pytest.raises(ValueError, match="spec_json contains plaintext secret"):
        ResourceDefinition(
            kind=ResourceKind.PLUGIN,
            id="leaky-provider",
            tenant_id="tenant-a",
            version="1",
            spec_json={"api_key": "sk-plaintext-leaked"},
        )


@pytest.mark.asyncio
async def test_s03_credential_ref_must_be_secret_ref() -> None:
    # RULE-EXT-04：credential 走 SecretRef/Binding——ResourceBinding.validate_binding
    # 拒绝非 secret:// 的 credential_ref（明文 credential 不得入 binding）。
    with pytest.raises(ValueError, match="credential_ref must use secret://"):
        ResourceBinding(
            binding_id="b-bad",
            tenant_id="tenant-a",
            subject_type=SubjectType.USER,
            subject_id="user-a",
            resource_type=ResourceKind.PLUGIN,
            resource_id="secret-provider",
            credential_ref="plaintext-not-a-secret-ref",
        )
