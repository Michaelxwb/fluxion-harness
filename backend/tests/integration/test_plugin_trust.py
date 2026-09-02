from __future__ import annotations

import pytest

from fluxion.plugins.contracts import (
    PluginContext,
    PluginExecutionMode,
    PluginManifest,
    PluginType,
    TrustLevel,
)
from fluxion.plugins.loader import PluginLoader, PluginLoadError, PluginTrustError
from fluxion.plugins.model_provider import ModelProviderRegistry


class UntrustedInProcessPlugin:
    manifest = PluginManifest(
        plugin_id="untrusted.local",
        version="1",
        plugin_type=PluginType.TOOL_EXECUTOR,
        entrypoint="tests.untrusted:Plugin",
        trust_level=TrustLevel.UNTRUSTED,
        permissions=[],
        dependencies=[],
        compatibility={"fluxion": ">=0.1"},
        execution_mode=PluginExecutionMode.IN_PROCESS,
    )

    async def setup(self, ctx: PluginContext) -> None:
        del ctx

    async def shutdown(self) -> None:
        return None


class MinimalPlugin:
    manifest = PluginManifest(
        plugin_id="minimal",
        version="1",
        plugin_type=PluginType.TOOL_EXECUTOR,
        entrypoint="tests.minimal:Plugin",
        trust_level=TrustLevel.TRUSTED,
        permissions=[],
        dependencies=[],
        compatibility={"fluxion": ">=0.1"},
        execution_mode=PluginExecutionMode.IN_PROCESS,
    )

    async def setup(self, ctx: PluginContext) -> None:
        del ctx

    async def shutdown(self) -> None:
        return None


class BrokenModelPlugin:
    manifest = PluginManifest(
        plugin_id="broken.model",
        version="1",
        plugin_type=PluginType.MODEL_PROVIDER,
        entrypoint="tests.broken:Plugin",
        trust_level=TrustLevel.TRUSTED,
        permissions=[],
        dependencies=[],
        compatibility={"fluxion": ">=0.1"},
        execution_mode=PluginExecutionMode.IN_PROCESS,
    )

    async def setup(self, ctx: PluginContext) -> None:
        del ctx

    async def shutdown(self) -> None:
        return None


@pytest.mark.asyncio
async def test_E_R05_untrusted_plugin_cannot_load_in_process() -> None:
    loader = PluginLoader()

    with pytest.raises(PluginTrustError) as error:
        await loader.load(UntrustedInProcessPlugin())

    assert error.value.plugin_id == "untrusted.local"
    assert "in-process" in str(error.value)


@pytest.mark.asyncio
async def test_E_R05_duplicate_plugin_id_is_rejected() -> None:
    loader = PluginLoader()
    await loader.load(MinimalPlugin())

    with pytest.raises(PluginLoadError, match="already loaded"):
        await loader.load(MinimalPlugin())

    assert len(loader.loaded) == 1


@pytest.mark.asyncio
async def test_E_R05_failed_registration_rolls_back_partial_state() -> None:
    registry = ModelProviderRegistry()
    loader = PluginLoader(model_provider_registry=registry)

    with pytest.raises(PluginLoadError, match="lacks complete"):
        await loader.load(BrokenModelPlugin())

    assert loader.loaded == []
    assert registry.provider_ids() == []
