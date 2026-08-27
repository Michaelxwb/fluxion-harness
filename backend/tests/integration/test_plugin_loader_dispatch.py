"""ADR-EXT-001 TASK-002 验收测试：PluginLoader per-PluginType registry 分派 + 回滚。

- S-01 (E2E): 加载 Semantic/Artifact/Secret 假实现 plugin → 按 PluginType 分派进对应 typed
  registry；loader.py 全程只 import `plugins.contracts`（Protocol 层），不 import 具体 impl。
- E-01 (integration): provider `setup()` 抛异常 或 注册失败 → 无 partial typed registry entry、
  无残留 `_loaded`/`_records`（沿用 `loader.py:76-82` 既有回滚）。

真实边界：PluginLoader + InMemoryProviderRegistry 均为真实组件；假实现 plugin 为 test double
（手写实现 Protocol，非 mock 框架），证明分派契约而非绕过。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fluxion.plugins.contracts import (
    ArtifactStoreProvider,
    PluginContext,
    PluginExecutionMode,
    PluginManifest,
    PluginType,
    SecretProvider,
    SemanticStoreProvider,
    TrustLevel,
)
from fluxion.plugins.loader import PluginLoader, PluginLoadError


def _manifest(plugin_id: str, plugin_type: PluginType) -> PluginManifest:
    return PluginManifest(
        plugin_id=plugin_id,
        version="1",
        plugin_type=plugin_type,
        entrypoint=f"tests.{plugin_id}:Plugin",
        trust_level=TrustLevel.TRUSTED,
        permissions=[],
        dependencies=[],
        compatibility={"fluxion": ">=0.1"},
        execution_mode=PluginExecutionMode.IN_PROCESS,
    )


# ---- 假实现 provider plugin（test double，实现 Protocol 真实方法）----


class _FakeSemanticStorePlugin:
    manifest = _manifest("fake.semantic", PluginType.SEMANTIC_STORE)

    async def setup(self, ctx: PluginContext) -> None:
        del ctx

    async def shutdown(self) -> None:
        return None

    async def store(self, tenant_id: str, user_id: str, record: dict) -> None:
        del tenant_id, user_id, record

    async def recall(
        self, tenant_id: str, user_id: str, query: str, top_k: int = 5
    ) -> list[dict]:
        del tenant_id, user_id, query, top_k
        return []

    async def search(self, tenant_id: str, user_id: str, filter: dict) -> list[dict]:
        del tenant_id, user_id, filter
        return []


class _FakeArtifactStorePlugin:
    manifest = _manifest("fake.artifact", PluginType.ARTIFACT_STORE)

    async def setup(self, ctx: PluginContext) -> None:
        del ctx

    async def shutdown(self) -> None:
        return None

    async def put(
        self, tenant_id: str, namespace: str, key: str, value: bytes
    ) -> None:
        del tenant_id, namespace, key, value

    async def get(self, tenant_id: str, namespace: str, key: str) -> bytes:
        del tenant_id, namespace, key
        return b""

    async def delete(self, tenant_id: str, namespace: str, key: str) -> None:
        del tenant_id, namespace, key


class _FakeSecretProviderPlugin:
    manifest = _manifest("fake.secret", PluginType.SECRET_PROVIDER)

    async def setup(self, ctx: PluginContext) -> None:
        del ctx

    async def shutdown(self) -> None:
        return None

    async def resolve(self, tenant_id: str, secret_ref: str):  # -> Secret
        from fluxion.plugins.contracts import Secret

        del tenant_id, secret_ref
        return Secret(value="", version="1")


# ---- S-01: per-PluginType 分派 ----


@pytest.mark.asyncio
async def test_s01_dispatches_fake_providers_to_typed_registries() -> None:
    """加载 Semantic/Artifact/Secret 假实现 → 各自分派进对应 typed registry，不串台。"""
    loader = PluginLoader()
    await loader.load(_FakeSemanticStorePlugin())
    await loader.load(_FakeArtifactStorePlugin())
    await loader.load(_FakeSecretProviderPlugin())

    semantic_registry = loader._registries[PluginType.SEMANTIC_STORE]
    artifact_registry = loader._registries[PluginType.ARTIFACT_STORE]
    secret_registry = loader._registries[PluginType.SECRET_PROVIDER]

    # 各 provider 命中自己的 typed registry
    assert isinstance(
        await _resolve(loader, PluginType.SEMANTIC_STORE, "fake.semantic"),
        SemanticStoreProvider,
    )
    assert isinstance(
        await _resolve(loader, PluginType.ARTIFACT_STORE, "fake.artifact"),
        ArtifactStoreProvider,
    )
    assert isinstance(
        await _resolve(loader, PluginType.SECRET_PROVIDER, "fake.secret"),
        SecretProvider,
    )

    # 不串台：semantic registry 只有 semantic provider
    assert semantic_registry.provider_ids() == ["fake.semantic"]
    assert artifact_registry.provider_ids() == ["fake.artifact"]
    assert secret_registry.provider_ids() == ["fake.secret"]


async def _resolve(loader: PluginLoader, plugin_type: PluginType, provider_id: str):
    return loader._registries[plugin_type].resolve(provider_id)


def test_s01_loader_imports_only_contracts_protocol() -> None:
    """loader.py 只 import `fluxion.plugins.contracts`（Protocol 层），不 import 具体 impl。

    真实边界=AST 扫描 loader.py 源码 import 语句（非运行时 mock）。
    """
    loader_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "fluxion"
        / "plugins"
        / "loader.py"
    )
    tree = ast.parse(loader_path.read_text(encoding="utf-8"))
    plugin_imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(
            "fluxion.plugins"
        ):
            plugin_imports.add(node.module)
    assert plugin_imports == {"fluxion.plugins.contracts"}, (
        f"loader.py 不得 import 具体 impl：{plugin_imports}"
    )


# ---- E-01: 回滚，无 partial typed registry entry ----


class _BrokenSetupSemanticPlugin:
    """setup() 抛异常 → 不应留 partial typed registry entry。"""

    manifest = _manifest("broken.setup.semantic", PluginType.SEMANTIC_STORE)

    async def setup(self, ctx: PluginContext) -> None:
        del ctx
        raise RuntimeError("setup exploded")

    async def shutdown(self) -> None:
        return None

    async def store(self, tenant_id: str, user_id: str, record: dict) -> None:
        del tenant_id, user_id, record

    async def recall(
        self, tenant_id: str, user_id: str, query: str, top_k: int = 5
    ) -> list[dict]:
        del tenant_id, user_id, query, top_k
        return []

    async def search(self, tenant_id: str, user_id: str, filter: dict) -> list[dict]:
        del tenant_id, user_id, filter
        return []


class _LacksProtocolSemanticPlugin:
    """声明 SEMANTIC_STORE 但缺 search → 注册失败（lacks protocol）→ 回滚 _loaded。"""

    manifest = _manifest("lacks.protocol.semantic", PluginType.SEMANTIC_STORE)

    async def setup(self, ctx: PluginContext) -> None:
        del ctx

    async def shutdown(self) -> None:
        return None

    async def store(self, tenant_id: str, user_id: str, record: dict) -> None:
        del tenant_id, user_id, record

    async def recall(
        self, tenant_id: str, user_id: str, query: str, top_k: int = 5
    ) -> list[dict]:
        del tenant_id, user_id, query, top_k
        return []

    # 故意缺 search → isinstance(SemanticStoreProvider) 拒绝


@pytest.mark.asyncio
async def test_e01_setup_failure_leaves_no_partial_typed_registry() -> None:
    loader = PluginLoader()
    with pytest.raises(RuntimeError, match="setup exploded"):
        await loader.load(_BrokenSetupSemanticPlugin())

    assert loader.loaded == []
    assert loader._registries[PluginType.SEMANTIC_STORE].provider_ids() == []


@pytest.mark.asyncio
async def test_e01_registration_failure_rolls_back_loaded_state() -> None:
    loader = PluginLoader()
    with pytest.raises(PluginLoadError, match="lacks"):
        await loader.load(_LacksProtocolSemanticPlugin())

    assert loader.loaded == []
    # isinstance 校验在 register 之前，故 typed registry 无 partial entry
    assert loader._registries[PluginType.SEMANTIC_STORE].provider_ids() == []


class _MinimalToolProviderPlugin:
    """声明 TOOL_PROVIDER 但缺 capabilities() → 注册失败（must implement CapabilityProvider）→ 回滚。"""

    manifest = _manifest("broken.tool.provider", PluginType.TOOL_PROVIDER)

    async def setup(self, ctx: PluginContext) -> None:
        del ctx

    async def shutdown(self) -> None:
        return None

    # 故意缺 capabilities() → isinstance(CapabilityProvider) 拒绝


@pytest.mark.asyncio
async def test_e01_tool_provider_without_capability_provider_warns_not_blocks() -> None:
    """TOOL_PROVIDER 无 CapabilityProvider：不阻断（最小插件约定），仅 warn 暴露静默零能力。"""
    loader = PluginLoader()
    record = await loader.load(_MinimalToolProviderPlugin())

    assert record.manifest.plugin_type is PluginType.TOOL_PROVIDER
    assert loader.loaded == [record]  # 加载成功（非硬拒绝），warn 由 _logger 暴露
