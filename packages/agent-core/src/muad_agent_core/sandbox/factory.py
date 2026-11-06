"""SandboxRunner 工厂：平台检测、bwrap 探测与显式 dev-unsafe 开关。"""

from __future__ import annotations

import logging
import sys
from enum import StrEnum
from typing import Protocol

from .bwrap import MIN_BWRAP_VERSION, BwrapRunner, probe_bwrap
from .runner import DevUnsafeRunner, DisabledRunner, SandboxEvent, SandboxRunner
from .spec import SandboxUnavailable

logger = logging.getLogger("muad_agent_core.sandbox")


class SandboxMode(StrEnum):
    BWRAP = "bwrap"
    DEV_UNSAFE = "dev-unsafe"
    DISABLED = "disabled"


class SandboxObserver(Protocol):
    def __call__(self, event: SandboxEvent) -> None: ...


def _notify(
    observer: SandboxObserver | None,
    kind: str,
    detail: dict[str, str],
    *,
    level: int = logging.INFO,
) -> None:
    logger.log(level, "sandbox_event kind=%s detail=%s", kind, detail)
    if observer is not None:
        observer(SandboxEvent(kind=kind, detail=dict(detail)))


def build_sandbox_runner(
    *,
    mode: str | SandboxMode,
    env: str,
    platform: str | None = None,
    bwrap_path: str | None = None,
    observer: SandboxObserver | None = None,
    min_bwrap_version: tuple[int, int, int] | None = None,
) -> SandboxRunner:
    """装配 runner。任何无法满足隔离的配置都抛 ``SandboxUnavailable``，绝不回退宿主执行。"""
    platform = platform or sys.platform
    normalized_env = str(env).strip().lower()
    try:
        normalized_mode = SandboxMode(str(mode).strip().lower())
    except ValueError as exc:
        _notify(
            observer,
            "sandbox_unavailable",
            {"mode": str(mode), "platform": platform, "reason": "unknown mode"},
            level=logging.WARNING,
        )
        raise SandboxUnavailable(f"unknown sandbox mode: {mode!r}") from exc

    if normalized_mode is SandboxMode.DEV_UNSAFE:
        if normalized_env != "dev":
            _notify(
                observer,
                "sandbox_unavailable",
                {"mode": normalized_mode.value, "platform": platform, "reason": "ENV is not dev"},
                level=logging.WARNING,
            )
            raise SandboxUnavailable("dev-unsafe is only allowed when ENV=dev")
        return DevUnsafeRunner(observer=observer)

    if normalized_mode is SandboxMode.DISABLED:
        if normalized_env != "dev":
            _notify(
                observer,
                "sandbox_unavailable",
                {"mode": normalized_mode.value, "platform": platform, "reason": "ENV is not dev"},
                level=logging.WARNING,
            )
            raise SandboxUnavailable("sandbox disabled is only allowed when ENV=dev")
        return DisabledRunner(observer=observer)

    if platform != "linux":
        _notify(
            observer,
            "sandbox_unavailable",
            {
                "mode": normalized_mode.value,
                "platform": platform,
                "reason": "bwrap requires Linux",
            },
            level=logging.WARNING,
        )
        raise SandboxUnavailable(
            f"bwrap sandbox is unavailable on {platform}; set sandbox_mode=dev-unsafe "
            "explicitly for local development (production never falls back)"
        )

    try:
        probe = probe_bwrap(
            bwrap_path, min_version=min_bwrap_version or MIN_BWRAP_VERSION
        )
    except SandboxUnavailable as exc:
        _notify(
            observer,
            "sandbox_unavailable",
            {
                "mode": normalized_mode.value,
                "platform": platform,
                "reason": str(exc),
            },
            level=logging.WARNING,
        )
        raise
    return BwrapRunner(probe, observer=observer)


def build_sandbox_runner_from_settings(
    settings: object,
    *,
    platform: str | None = None,
    observer: SandboxObserver | None = None,
) -> SandboxRunner:
    """从 ``SharedSettings`` 装配；这里只读取四个启动项，不把整份配置传给 runner。"""
    return build_sandbox_runner(
        mode=getattr(settings, "sandbox_mode", "bwrap"),
        env=getattr(settings, "env", "dev"),
        platform=platform,
        bwrap_path=getattr(settings, "sandbox_bwrap_path", None),
        observer=observer,
    )


__all__ = [
    "SandboxEvent",
    "SandboxMode",
    "SandboxObserver",
    "build_sandbox_runner",
    "build_sandbox_runner_from_settings",
]
