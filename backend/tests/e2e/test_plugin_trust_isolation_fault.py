"""ADR-EXT-001 TASK-005 验收测试：Trust 分派 + 故障隔离 + typed manifest 超时/失败策略。

S-04（E2E，RULE-fluxion-dfx-001 + RULE-EXT-03）：

- 真实边界：trust_level → execution_mode 分派（`_enforce_trust`）+ fault injection
  （setup crash 不拖垮其他 plugin + loader 状态干净）+ Hook typed timeout/fail_policy/
  scope 形状（`HookRegistryProtocol` + ADR-007 `HookRegistration`）。
- 断言 1：untrusted plugin 走 isolated（execution_mode=ISOLATED）可加载——ADR-010
  `_enforce_trust` 只拒 untrusted+in_process，不拒 untrusted+isolated（强制 untrusted
  必须非 in_process = isolated）。
- 断言 2：fault injection——单 plugin setup crash 不拖垮 Runtime：已加载的其他 plugin
  仍可用、loader 状态干净（crash plugin 无残留 `_loaded`/`_records`）、可继续
  `shutdown_all`。RULE-EXT-03：isolation 由 ADR-010 既有机制强制，本 ADR 不重决。
- 断言 3：每保留类型 typed 治理形状——Hook 类型经 `HookRegistryProtocol` 承载
  priority/timeout_ms/fail_policy/scope（ADR-007 `HookRegistration`，§99 已有 typed
  hook，本 ADR 对齐入统一模型）；HOOK 类型在统一模型不进 typed provider registry
  （`_PROVIDER_PROTOCOL` 不含 HOOK → 走 HookRegistryProtocol，§3.4 L323）；其他类型
  trust/isolation 由 `_enforce_trust` 强制（ADR-010）；具体 timeout/fail_policy/scope
  值 Rolling-wave（§307），本 ADR 不重决（RULE-EXT-03）。

RED 约定（cf-task:start #7）：已有行为补测——产品原语（`_enforce_trust`/`TrustLevel`/
`execution_mode` ADR-010 + `HookRegistration` priority/timeout_ms/fail_policy/scope
ADR-007 + `HookRegistryProtocol` 形状 + setup 异常传播 + per-plugin `_loaded` 隔离 +
回滚 + PluginLoader per-PluginType 泛化分派 TASK-002）在 ADR-010/007 + TASK-001/002
已落地，S-04 E2E 验证为 green-before；真实 RED 由 ADR-010/007 契约定义承载。不得伪造失败。
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from fluxion.kernel.events import HookRegistration
from fluxion.plugins.contracts import (
    HookRegistryProtocol,
    PluginContext,
    PluginExecutionMode,
    PluginManifest,
    PluginType,
    TrustLevel,
)
from fluxion.plugins.loader import PluginLoader, _PROVIDER_PROTOCOL


def _manifest(
    plugin_id: str,
    *,
    plugin_type: PluginType = PluginType.TOOL_PROVIDER,
    trust: TrustLevel = TrustLevel.TRUSTED,
    execution_mode: PluginExecutionMode = PluginExecutionMode.IN_PROCESS,
) -> PluginManifest:
    return PluginManifest(
        plugin_id=plugin_id,
        version="1",
        plugin_type=plugin_type,
        entrypoint=f"tests.{plugin_id}:Plugin",
        trust_level=trust,
        permissions=[],
        dependencies=[],
        compatibility={"fluxion": ">=0.1"},
        execution_mode=execution_mode,
    )


class _UntrustedIsolatedPlugin:
    """untrusted + ISOLATED：ADR-010 允许（untrusted 走 isolated，非 in_process）。"""

    manifest = _manifest(
        "untrusted.isolated",
        trust=TrustLevel.UNTRUSTED,
        execution_mode=PluginExecutionMode.ISOLATED,
    )

    async def setup(self, ctx: PluginContext) -> None:
        del ctx

    async def shutdown(self) -> None:
        return None


class _TrustedOkPlugin:
    """trusted + in_process：正常加载，用作 fault injection 的"幸存"plugin。"""

    manifest = _manifest("trusted.ok")

    async def setup(self, ctx: PluginContext) -> None:
        del ctx

    async def shutdown(self) -> None:
        return None


class _SetupCrashPlugin:
    """fault injection：setup() 抛异常，模拟单 plugin crash。"""

    manifest = _manifest("crash.setup")

    async def setup(self, ctx: PluginContext) -> None:
        del ctx
        raise RuntimeError("simulated plugin setup crash")

    async def shutdown(self) -> None:
        return None


# ---- 断言 1：untrusted 走 isolated ----


@pytest.mark.asyncio
async def test_s04_untrusted_plugin_runs_isolated() -> None:
    loader = PluginLoader()
    # ADR-010 _enforce_trust 只拒 untrusted+in_process；untrusted+isolated 允许加载
    record = await loader.load(_UntrustedIsolatedPlugin())

    assert record.manifest.trust_level == TrustLevel.UNTRUSTED
    assert record.manifest.execution_mode == PluginExecutionMode.ISOLATED
    assert len(loader.loaded) == 1

    # Runtime 仍可正常 shutdown_all（lifecycle 完整）
    await loader.shutdown_all()
    assert loader.loaded == []


# ---- 断言 2：fault injection 单 plugin crash 不拖垮 Runtime ----


@pytest.mark.asyncio
async def test_s04_single_plugin_setup_crash_does_not_take_down_runtime() -> None:
    loader = PluginLoader()

    # 先加载幸存 plugin A（trusted, setup ok）
    await loader.load(_TrustedOkPlugin())
    assert len(loader.loaded) == 1

    # fault injection：加载 plugin B，setup() crash
    with pytest.raises(RuntimeError, match="simulated plugin setup crash"):
        await loader.load(_SetupCrashPlugin())

    # 单 plugin crash 不拖垮 Runtime：A 仍在 + B 无残留 _loaded/_records
    assert len(loader.loaded) == 1
    assert loader.loaded[0].manifest.plugin_id == "trusted.ok"
    assert "crash.setup" not in loader._loaded
    assert "crash.setup" not in loader._records

    # Runtime 仍可正常 shutdown_all（A 的 lifecycle 完整，B 未半加载）
    await loader.shutdown_all()
    assert loader.loaded == []


# ---- 断言 3：Hook typed timeout/fail_policy/scope 形状 ----


def test_s04_hook_typed_carries_timeout_fail_policy_scope() -> None:
    # SPI-06 HookRegistryProtocol 形状（§99 已有 typed hook，本 ADR 对齐入统一模型）
    assert hasattr(HookRegistryProtocol, "register")
    assert hasattr(HookRegistryProtocol, "ordered")

    # ADR-007 HookRegistration 携带 typed priority/timeout_ms/fail_policy/scope
    registration_fields = {f.name for f in fields(HookRegistration)}
    assert {"priority", "timeout_ms", "fail_policy", "scope"}.issubset(
        registration_fields
    )

    # HOOK 类型在统一模型不进 typed provider registry（走 HookRegistryProtocol，§3.4 L323）
    assert PluginType.HOOK not in _PROVIDER_PROTOCOL
