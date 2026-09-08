"""ADR-EXT-001 TASK-005 验收测试：Trust 分派 + 故障隔离 + typed manifest 超时/失败策略。

S-04（E2E，RULE-fluxion-dfx-001 + RULE-EXT-03）：

- 真实边界：trust_level → execution_mode 分派（`_enforce_trust`）+ fault injection
  （setup crash 不拖垮其他 plugin + loader 状态干净）+ Hook typed timeout/fail_policy
  形状（`HookRegistryProtocol` + `HookRegistration`）。
- 断言 1（TASK-004 新契约）：`ISOLATED` 已从 Contract 删除（假隔离不可接受）；
  untrusted 任何组合被 `_enforce_trust` 拒绝进进程；V1 只接受 TRUSTED + IN_PROCESS。
- 断言 2：fault injection——单 plugin setup crash 不拖垮 Runtime：已加载的其他 plugin
  仍可用、loader 状态干净（crash plugin 无残留 `_loaded`/`_records`）、可继续
  `shutdown_all`。
- 断言 3：每保留类型 typed 治理形状——Hook 类型经 `HookRegistryProtocol` 承载
  priority/timeout_ms/fail_policy（`scope` 已随 HookScope 删除，不再断言）；
  HOOK 类型在统一模型不进 typed provider registry（`_PROVIDER_PROTOCOL` 不含
  HOOK → 走 HookRegistryProtocol）；其他类型 trust 由 `_enforce_trust` 强制；
  `ordered` 仍在协议上（P2-01/TASK-005 待收敛为纯 register，此处只做现状注记）。

RED 约定（cf-task:start #7）：TASK-004 为契约删除——`test_s04_isolated_mode_no_longer_exists`
对旧行为 RED（ISOLATED 仍存在即失败）；`test_s04_untrusted_plugin_is_rejected` 为
pin（旧代码已拒 untrusted+in_process，前后皆过，记录无行为变化，不伪造失败）。
另：断言 3 的 `scope` 子集在 main 已失败（TASK-007 删字段后残留，pre-existing），
本次一并修正为真实五字段。
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from fluxion.kernel.events import HookRegistration
from fluxion.plugins.contracts import (
    HookRegistrationSinkProtocol,
    PluginContext,
    PluginExecutionMode,
    PluginManifest,
    PluginType,
    TrustLevel,
)
from fluxion.plugins.loader import _PROVIDER_PROTOCOL, PluginLoader, PluginTrustError


def _manifest(
    plugin_id: str,
    *,
    plugin_type: PluginType = PluginType.TOOL_EXECUTOR,
    trust: TrustLevel = TrustLevel.TRUSTED,
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
    )


class _UntrustedPlugin:
    """untrusted（任何组合）：TASK-004 新契约——必须被拒进进程。"""

    manifest = _manifest("untrusted.plugin", trust=TrustLevel.UNTRUSTED)

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


# ---- 断言 1（TASK-004 新契约）：假 ISOLATED 删除＋不可信拒绝 ----


@pytest.mark.asyncio
async def test_s04_isolated_mode_no_longer_exists() -> None:
    """S-ISO-01：ISOLATED 已从 Contract 删除，旧值明确失败。"""
    assert not hasattr(PluginExecutionMode, "ISOLATED")
    assert "isolated" not in {mode.value for mode in PluginExecutionMode}
    with pytest.raises(ValueError):
        PluginExecutionMode("isolated")


@pytest.mark.asyncio
async def test_s04_untrusted_plugin_is_rejected() -> None:
    """S-ISO-03：untrusted 插件被拒进进程，loader 无残留。"""
    loader = PluginLoader()
    with pytest.raises(PluginTrustError):
        await loader.load(_UntrustedPlugin())
    assert loader.loaded == []


@pytest.mark.asyncio
async def test_s04_trusted_in_process_loads_unaffected() -> None:
    """S-ISO-02：TRUSTED + IN_PROCESS 正常组合加载不受影响。"""
    loader = PluginLoader()
    record = await loader.load(_TrustedOkPlugin())
    assert record.manifest.trust_level == TrustLevel.TRUSTED
    assert record.manifest.execution_mode == PluginExecutionMode.IN_PROCESS
    assert len(loader.loaded) == 1
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


# ---- 断言 3（TASK-005 新契约）：Hook 注册接收端形状 ----


def test_s04_hook_sink_carries_timeout_fail_policy() -> None:
    """S-SPI-01：Plugin SPI 仅暴露 register（纯注册接收端）；排序留 Kernel。"""
    # SPI-06 收敛为 HookRegistrationSinkProtocol：只有 register，没有 ordered。
    assert hasattr(HookRegistrationSinkProtocol, "register")
    assert not hasattr(HookRegistrationSinkProtocol, "ordered")

    # HookRegistration 真实字段（scope 已随 HookScope 删除，不再断言）
    registration_fields = {f.name for f in fields(HookRegistration)}
    assert {"priority", "timeout_ms", "fail_policy"}.issubset(registration_fields)
    assert "scope" not in registration_fields

    # HOOK 类型在统一模型不进 typed provider registry（走注册接收端）
    assert PluginType.HOOK not in _PROVIDER_PROTOCOL
