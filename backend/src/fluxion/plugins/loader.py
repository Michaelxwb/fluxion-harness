from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass

from fluxion.plugins.contracts import (
    CapabilityDescriptor,
    CapabilityProvider,
    ModelProvider,
    ModelProviderRegistryProtocol,
    Plugin,
    PluginContext,
    PluginExecutionMode,
    PluginManifest,
    PluginType,
    TrustLevel,
)


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

    @property
    def loaded(self) -> list[LoadedPlugin]:
        return list(self._records.values())

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
            self._register_model_provider(plugin, manifest, capabilities)
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
