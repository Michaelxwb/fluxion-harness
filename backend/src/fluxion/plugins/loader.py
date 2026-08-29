from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import dataclass

from fluxion.plugins.contracts import (
    ArtifactStoreProvider,
    CapabilityDescriptor,
    CapabilityProvider,
    ModelProvider,
    ModelProviderRegistryProtocol,
    Plugin,
    PluginContext,
    PluginExecutionMode,
    PluginManifest,
    PluginType,
    ProviderNotFoundError,
    SecretProvider,
    SemanticStoreProvider,
    TrustLevel,
)

_logger = logging.getLogger(__name__)


class PluginLoadError(RuntimeError):
    code = "plugin_load_error"


class PluginTrustError(PluginLoadError):
    code = "plugin_trust_error"

    def __init__(self, plugin_id: str, message: str) -> None:
        self.plugin_id = plugin_id
        super().__init__(f"{plugin_id}: {message}")


@dataclass(frozen=True, slots=True)
class TrustPolicy:
    allow_untrusted_in_process: bool = False


@dataclass(frozen=True, slots=True)
class LoadedPlugin:
    manifest: PluginManifest
    capabilities: list[CapabilityDescriptor]


# ADR-EXT-001 统一扩展模型：per-PluginType 分派表。
# 仅含 4 个"可 resolve(provider_id)"的 provider SPI。TOOL_PROVIDER 走
# CapabilityProvider→LoadedPlugin.capabilities（既有路径，不进 typed registry）；
# HOOK 走 HookRegistryProtocol（对齐 ADR-007，Phase 5 注入），本阶段不分派。
_PROVIDER_PROTOCOL: dict[PluginType, type] = {
    PluginType.MODEL_PROVIDER: ModelProvider,
    PluginType.SEMANTIC_STORE: SemanticStoreProvider,
    PluginType.ARTIFACT_STORE: ArtifactStoreProvider,
    PluginType.SECRET_PROVIDER: SecretProvider,
}


class InMemoryProviderRegistry:
    """参考实现的 in-memory typed provider registry（register/resolve/provider_ids）。

    生产实现（pgvector / S3 / SecretProvider resolve 等）按 design §11 Rolling-wave
    延后 Phase 1/5；此处只提供形状一致的参考 registry，供 PluginLoader 分派。
    """

    def __init__(self) -> None:
        self._providers: dict[str, object] = {}

    def register(self, provider_id: str, provider: object) -> None:
        self._providers[provider_id] = provider

    def resolve(self, provider_id: str) -> object:
        try:
            return self._providers[provider_id]
        except KeyError as error:
            raise ProviderNotFoundError(
                f"provider '{provider_id}' not registered in {type(self).__name__}"
            ) from error

    def provider_ids(self) -> list[str]:
        return list(self._providers)


class PluginLoader:
    def __init__(
        self,
        *,
        trust_policy: TrustPolicy | None = None,
        model_provider_registry: ModelProviderRegistryProtocol | None = None,
    ) -> None:
        self._trust_policy = trust_policy or TrustPolicy()
        self._model_provider_registry = model_provider_registry
        self._loaded: dict[str, Plugin] = {}
        self._records: dict[str, LoadedPlugin] = {}
        # 非 MODEL_PROVIDER 的 typed provider SPI：per-PluginType 参考 registry。
        self._registries: dict[PluginType, InMemoryProviderRegistry] = {
            pt: InMemoryProviderRegistry()
            for pt in _PROVIDER_PROTOCOL
            if pt is not PluginType.MODEL_PROVIDER
        }

    @property
    def loaded(self) -> list[LoadedPlugin]:
        return list(self._records.values())

    def registry_for(self, plugin_type: PluginType) -> InMemoryProviderRegistry:
        """只读访问 per-PluginType 参考 registry（Phase 5 E506 lifecycle 可观测）。"""
        return self._registries[plugin_type]

    async def load(
        self,
        plugin: Plugin,
        ctx: PluginContext | None = None,
    ) -> LoadedPlugin:
        manifest = plugin.manifest
        if manifest.plugin_id in self._loaded:
            raise PluginLoadError(f"plugin {manifest.plugin_id} already loaded")
        self._enforce_trust(manifest)
        await plugin.setup(ctx or PluginContext())
        try:
            capabilities = _capabilities(plugin)
            self._loaded[manifest.plugin_id] = plugin
            record = LoadedPlugin(manifest=manifest, capabilities=capabilities)
            self._records[manifest.plugin_id] = record
            self._register_provider(plugin, manifest, capabilities)
            return record
        except Exception:
            # 注册中途失败时回滚，避免残留部分注册
            self._loaded.pop(manifest.plugin_id, None)
            self._records.pop(manifest.plugin_id, None)
            with suppress(Exception):
                await plugin.shutdown()
            raise

    async def shutdown_all(self) -> None:
        for plugin_id in tuple(self._loaded):
            await self._loaded[plugin_id].shutdown()
            self._loaded.pop(plugin_id, None)
            self._records.pop(plugin_id, None)

    def _enforce_trust(self, manifest: PluginManifest) -> None:
        untrusted = manifest.trust_level is TrustLevel.UNTRUSTED
        in_process = manifest.execution_mode is PluginExecutionMode.IN_PROCESS
        if untrusted and in_process and not self._trust_policy.allow_untrusted_in_process:
            raise PluginTrustError(
                manifest.plugin_id,
                "untrusted plugin cannot run in-process",
            )

    def _register_model_provider(
        self,
        plugin: Plugin,
        manifest: PluginManifest,
        capabilities: list[CapabilityDescriptor],
    ) -> None:
        if self._model_provider_registry is None:
            return
        if manifest.plugin_type is not PluginType.MODEL_PROVIDER:
            return
        if not isinstance(plugin, ModelProvider):
            raise PluginLoadError(f"{manifest.plugin_id}: model plugin lacks complete()")
        provider_id = _provider_id(manifest, capabilities)
        self._model_provider_registry.register(provider_id, plugin)

    def _register_provider(
        self,
        plugin: Plugin,
        manifest: PluginManifest,
        capabilities: list[CapabilityDescriptor],
    ) -> None:
        """per-PluginType 分派：MODEL_PROVIDER 走注入 registry；其余 typed SPI 走参考 registry。"""
        if manifest.plugin_type is PluginType.MODEL_PROVIDER:
            self._register_model_provider(plugin, manifest, capabilities)
            return
        if manifest.plugin_type is PluginType.TOOL_PROVIDER:
            # TOOL_PROVIDER = CapabilityProvider 形状（ADR-EXT-001 SPI-02）。不硬拒绝：
            # 既有最小插件用 TOOL_PROVIDER 作通用类型（无 provider 角色）；但静默零能力
            # 是误配置信号，故 warn 暴露（review #1：静默 → 显式警告）。
            if not isinstance(plugin, CapabilityProvider):
                _logger.warning(
                    "%s: tool_provider plugin lacks CapabilityProvider -> loads with zero capabilities",
                    manifest.plugin_id,
                )
            return
        protocol = _PROVIDER_PROTOCOL.get(manifest.plugin_type)
        if protocol is None:
            # HOOK 不进 typed provider registry（见 _PROVIDER_PROTOCOL 注释）
            return
        if not isinstance(plugin, protocol):
            raise PluginLoadError(
                f"{manifest.plugin_id}: {manifest.plugin_type.value} plugin lacks protocol"
            )
        provider_id = _provider_id(manifest, capabilities)
        self._registries[manifest.plugin_type].register(provider_id, plugin)


def _capabilities(plugin: Plugin) -> list[CapabilityDescriptor]:
    if isinstance(plugin, CapabilityProvider):
        return plugin.capabilities()
    return []


def _provider_id(
    manifest: PluginManifest,
    capabilities: list[CapabilityDescriptor],
) -> str:
    for capability in capabilities:
        if capability.kind == "model_provider":
            provider_id = capability.metadata.get("provider_id")
            if isinstance(provider_id, str) and provider_id.strip():
                return provider_id
    return manifest.plugin_id
