"""TASK-007：Hook Plugin 关闭失败时仍完成清理并保留重试能力。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace

import pytest

from fluxion.plugins.loader import PluginLoader, PluginShutdownError
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.services.runtime_app import RuntimeApplicationService
from tests.integration.test_plugin_lifecycle import _LifecycleHookPlugin
from tests.runtime_helpers import TEST_POSTGRES_DSN


class _FailOncePlugin(_LifecycleHookPlugin):
    def __init__(self, plugin_id: str) -> None:
        super().__init__(plugin_id)
        self._failed = False

    async def shutdown(self) -> None:
        self.shutdown_count += 1
        if not self._failed:
            self._failed = True
            raise RuntimeError("first shutdown failed")
        await self._close_resources()

    async def _close_resources(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        if self.client is not None:
            await self.client.close()


class _SlowShutdownPlugin(_LifecycleHookPlugin):
    async def shutdown(self) -> None:
        self.shutdown_count += 1
        await asyncio.sleep(30)


def _service() -> RuntimeApplicationService:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    return RuntimeApplicationService.create_dev_bundle(store)


@pytest.mark.asyncio
async def test_S_CL_01(monkeypatch: pytest.MonkeyPatch) -> None:
    """单个 shutdown 失败不跳过其余资源；第二次 close 只重试失败插件。"""
    failing = _FailOncePlugin("cleanup.fail-once")
    healthy = _LifecycleHookPlugin("cleanup.healthy")
    monkeypatch.setattr(
        "fluxion.plugins.loader.discover_hook_plugins", lambda: [failing, healthy]
    )
    service = _service()
    await service.initialize()

    with pytest.raises(PluginShutdownError, match="cleanup.fail-once"):
        await service.close()

    assert healthy.shutdown_count == 1
    assert healthy.task is not None and healthy.task.done()
    assert healthy.client is not None and healthy.client.closed
    assert service._hook_plugin_loader is not None
    assert [item.manifest.plugin_id for item in service._hook_plugin_loader.loaded] == [
        "cleanup.fail-once"
    ]

    await service.close()
    assert failing.shutdown_count == 2
    assert healthy.shutdown_count == 1
    assert service._hook_plugin_loader is None


@pytest.mark.asyncio
async def test_E_CL_01(monkeypatch: pytest.MonkeyPatch) -> None:
    """初始化错误保持主因；rollback 失败可重试，单插件关闭也有超时上限。"""
    failing = _FailOncePlugin("rollback.fail-once")
    monkeypatch.setattr(
        "fluxion.plugins.loader.discover_hook_plugins", lambda: [failing]
    )

    async def _init_failure() -> None:
        raise RuntimeError("memory init is the primary failure")

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    service = RuntimeApplicationService(
        store,
        memory_retriever=SimpleNamespace(
            provider=SimpleNamespace(initialize=_init_failure)
        ),  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="primary failure") as exc_info:
        await service.initialize()
    assert "rollback.fail-once" in "\n".join(exc_info.value.__notes__)
    assert service._hook_plugin_loader is not None
    await service.close()

    slow = _SlowShutdownPlugin("cleanup.timeout")
    healthy = _LifecycleHookPlugin("cleanup.after-timeout")
    loader = PluginLoader(shutdown_timeout_ms=5)
    await loader.load(slow)
    await loader.load(healthy)
    with pytest.raises(PluginShutdownError, match="cleanup.timeout"):
        await loader.shutdown_all()
    assert healthy.shutdown_count == 1
    assert [item.manifest.plugin_id for item in loader.loaded] == ["cleanup.timeout"]
    assert slow.task is not None
    slow.task.cancel()
    with suppress(asyncio.CancelledError):
        await slow.task

