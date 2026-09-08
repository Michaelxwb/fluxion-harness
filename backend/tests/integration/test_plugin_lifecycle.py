"""TASK-003（P1-03）：Runtime 持有 Loader 并闭环 shutdown/rollback。

真实边界：真实 RuntimeApplicationService.initialize/close（dev bundle＋PG）
＋真实 PluginLoader.load/shutdown_all ＋ LifecycleHookPlugin 测试插件
（setup 建 background task＋client，shutdown 关闭）。entry_points 经
monkeypatch 注入，无需真实安装包。
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace

import pytest

from fluxion.kernel.events import (
    BeforeToolCallPayload,
    EventPayload,
    FailPolicy,
    HookRegistration,
)
from fluxion.plugins.contracts import PluginContext, PluginManifest, PluginType, TrustLevel
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.services.runtime_app import RuntimeApplicationService
from tests.runtime_helpers import TEST_POSTGRES_DSN


class _FakeClient:
    """可观测关闭的客户端 stand-in（socket/HTTP client 的行为替身）。"""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _LifecycleHookPlugin:
    """setup 建后台任务＋client，shutdown 关闭并计数。"""

    def __init__(self, plugin_id: str = "lifecycle.hook") -> None:
        self.manifest = PluginManifest(
            plugin_id=plugin_id,
            version="1",
            plugin_type=PluginType.HOOK,
            entrypoint=f"tests.{plugin_id}:Plugin",
            trust_level=TrustLevel.TRUSTED,
            permissions=[],
            dependencies=[],
            compatibility={"fluxion": ">=0.1"},
        )
        self.setup_count = 0
        self.shutdown_count = 0
        self.task: asyncio.Task[None] | None = None
        self.client: _FakeClient | None = None

    def hook_registrations(self) -> list[object]:
        async def _observe(_payload: EventPayload) -> None:
            return None

        return [
            HookRegistration(
                registration_id=f"{self.manifest.plugin_id}.observe",
                event_type=BeforeToolCallPayload,
                priority=100,
                timeout_ms=1000,
                fail_policy=FailPolicy.FAIL_OPEN,
                handler=_observe,
            )
        ]

    async def setup(self, ctx: PluginContext) -> None:
        del ctx
        self.setup_count += 1
        self.client = _FakeClient()
        self.task = asyncio.create_task(self._worker())

    async def _worker(self) -> None:
        while True:
            await asyncio.sleep(3600)

    async def shutdown(self) -> None:
        self.shutdown_count += 1
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        if self.client is not None:
            await self.client.close()


class _SetupCrashPlugin(_LifecycleHookPlugin):
    async def setup(self, ctx: PluginContext) -> None:
        del ctx
        raise RuntimeError("simulated lifecycle setup crash")


def _service() -> RuntimeApplicationService:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    return RuntimeApplicationService.create_dev_bundle(store)


@pytest.mark.asyncio
async def test_S_PL_01_close_calls_plugin_shutdown_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-PL-01：正常 close 调用一次 plugin.shutdown。"""
    plugin = _LifecycleHookPlugin()
    monkeypatch.setattr(
        "fluxion.plugins.loader.discover_hook_plugins", lambda: [plugin]
    )
    service = _service()
    try:
        await service.initialize()
        assert plugin.setup_count == 1
        await service.close()
        assert plugin.shutdown_count == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_S_PL_02a_install_failure_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-PL-02a：安装中途失败 → 已加载插件被 rollback shutdown，不残留。"""
    good = _LifecycleHookPlugin("lifecycle.good")
    crash = _SetupCrashPlugin("lifecycle.crash")
    monkeypatch.setattr(
        "fluxion.plugins.loader.discover_hook_plugins", lambda: [good, crash]
    )
    service = _service()
    try:
        with pytest.raises(RuntimeError, match="simulated lifecycle setup crash"):
            await service.initialize()
        assert good.setup_count == 1
        assert good.shutdown_count == 1, "已加载插件必须被 rollback"
        assert service._hook_plugin_loader is None
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_S_PL_02b_memory_stage_failure_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-PL-02b：initialize 后续阶段失败 → 已加载插件被 rollback。"""

    async def _boom() -> None:
        raise RuntimeError("simulated memory provider init failure")

    plugin = _LifecycleHookPlugin()
    monkeypatch.setattr(
        "fluxion.plugins.loader.discover_hook_plugins", lambda: [plugin]
    )
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    service = RuntimeApplicationService(
        store,
        memory_retriever=SimpleNamespace(provider=SimpleNamespace(initialize=_boom)),  # type: ignore[arg-type]
    )
    try:
        with pytest.raises(RuntimeError, match="simulated memory provider"):
            await service.initialize()
        assert plugin.setup_count == 1
        assert plugin.shutdown_count == 1, "memory 阶段失败必须 rollback 已加载插件"
        assert service._hook_plugin_loader is None
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_S_PL_03_shutdown_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-PL-03：重复 close 不二次调用 shutdown、不抛错。"""
    plugin = _LifecycleHookPlugin()
    monkeypatch.setattr(
        "fluxion.plugins.loader.discover_hook_plugins", lambda: [plugin]
    )
    service = _service()
    try:
        await service.initialize()
        await service.close()
        await service.close()
        assert plugin.shutdown_count == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_E_PL_01_no_leftover_resources_after_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E-PL-01：close 后无残留 task/client，loader 已清空。"""
    plugin = _LifecycleHookPlugin()
    monkeypatch.setattr(
        "fluxion.plugins.loader.discover_hook_plugins", lambda: [plugin]
    )
    service = _service()
    try:
        await service.initialize()
        assert plugin.task is not None and not plugin.task.done()
        await service.close()
        assert plugin.task.done(), "后台任务必须被取消/结束"
        assert plugin.client is not None and plugin.client.closed, "client 必须关闭"
        assert service._hook_plugin_loader is None
    finally:
        await service.close()
