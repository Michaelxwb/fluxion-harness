"""Hook loader 接线（105 P1-02 / TASK-005）验收测试。

覆盖 S-04/E-02（loader 层）：
- `PluginType.HOOK` 经 PluginLoader 分派到 HookRegistry；
- `entry_points(group="fluxion.hooks")` 发现插件；
- 格式错误 fail-fast。
注：端到端 audit 行为由 TASK-006 最终验收。
"""

from __future__ import annotations

import pytest

from fluxion.kernel.events import (
    AfterToolCallPayload,
    BeforeToolCallPayload,
    FailPolicy,
    HookRegistration,
    HookScheduler,
    HookScope,
)
from fluxion.plugins.contracts import (
    PluginContext,
    PluginManifest,
    PluginType,
    TrustLevel,
)
from fluxion.plugins.loader import PluginLoader, discover_hook_plugins


async def _noop(_payload: object) -> None:
    return None


class _AuditHookPlugin:
    """fixture Hook 插件：提供 before/after tool 注册。"""

    def __init__(self) -> None:
        self.set_up = False
        self.torn_down = False

    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id="audit-hook",
            version="1",
            plugin_type=PluginType.HOOK,
            entrypoint="tests.fakes.audit_hook:plugin",
            trust_level=TrustLevel.TRUSTED,
            permissions=[],
            dependencies=[],
            compatibility={},
        )

    async def setup(self, ctx: PluginContext) -> None:
        self.set_up = True

    async def shutdown(self) -> None:
        self.torn_down = True

    def hook_registrations(self) -> list:
        return [
            HookRegistration(
                registration_id="audit-before-tool",
                event_type=BeforeToolCallPayload,
                priority=10,
                timeout_ms=100,
                fail_policy=FailPolicy.FAIL_OPEN,
                scope=HookScope.GLOBAL,
                handler=_noop,
            ),
            HookRegistration(
                registration_id="audit-after-tool",
                event_type=AfterToolCallPayload,
                priority=10,
                timeout_ms=100,
                fail_policy=FailPolicy.FAIL_OPEN,
                scope=HookScope.GLOBAL,
                handler=_noop,
            ),
        ]


def test_S_04_hook_plugin_dispatched_to_registry() -> None:
    """S-04（loader 层）：HOOK 插件分派到 registry，before/after 均可见。"""
    import asyncio

    scheduler = HookScheduler()
    loader = PluginLoader(hook_registry=scheduler)
    record = asyncio.run(loader.load(_AuditHookPlugin()))  # type: ignore[arg-type]
    assert record.manifest.plugin_id == "audit-hook"
    assert [
        registration.registration_id
        for registration in scheduler.ordered(BeforeToolCallPayload)
    ] == ["audit-before-tool"]
    assert [
        registration.registration_id
        for registration in scheduler.ordered(AfterToolCallPayload)
    ] == ["audit-after-tool"]


def test_S_04_hook_plugin_without_registrations_rejected() -> None:
    """S-04（loader 层）：HOOK 插件不提供注册即 fail-closed（防空挂）。"""
    import asyncio

    class _Empty:
        @property
        def manifest(self) -> PluginManifest:
            return PluginManifest(
                plugin_id="empty-hook",
                version="1",
                plugin_type=PluginType.HOOK,
                entrypoint="tests.fakes.empty:plugin",
                trust_level=TrustLevel.TRUSTED,
                permissions=[],
                dependencies=[],
                compatibility={},
            )

        async def setup(self, ctx: PluginContext) -> None:
            return None

        async def shutdown(self) -> None:
            return None

    loader = PluginLoader(hook_registry=HookScheduler())
    with pytest.raises(Exception, match="hook_registrations"):
        asyncio.run(loader.load(_Empty()))  # type: ignore[arg-type]


def test_S_04_discover_hooks_via_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    """S-04（loader 层）：entry_points 发现插件实例。"""
    from types import SimpleNamespace

    plugin = _AuditHookPlugin()
    entry = SimpleNamespace(name="audit-hook", load=lambda: plugin)
    monkeypatch.setattr(
        "fluxion.plugins.loader.entry_points", lambda group=None: (entry,)
    )
    found = discover_hook_plugins()
    assert found == [plugin]


def test_E_02_malformed_entry_point_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """E-02（loader 层）：格式错误 fail-fast，不静默跳过。"""
    from types import SimpleNamespace

    from fluxion.plugins.loader import PluginLoadError

    def _boom() -> object:
        raise ImportError("no such module")

    entry = SimpleNamespace(name="broken-hook", load=_boom)
    monkeypatch.setattr(
        "fluxion.plugins.loader.entry_points", lambda group=None: (entry,)
    )
    with pytest.raises(PluginLoadError, match="broken-hook"):
        discover_hook_plugins()
