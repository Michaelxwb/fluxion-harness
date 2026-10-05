"""压缩配置的读取缝：设置源（短 TTL 进程内缓存）+ Agent 覆盖 + 默认值，三层合并。

design §3.4/§3.5 的两条口径落在这里：
- **不每轮查库**——设置源读一次进进程内缓存，TTL 由 `CONTEXT_SETTINGS_CACHE_TTL_SEC` 定；
- **运行期可改的设置 ≤TTL 对下一个新 Run 生效**——在飞的 Run 用的仍是它自己那一份冻结值。

今天平台侧还没有设置表（Console 系统设置页是需求二），默认源返回空覆盖；把读取做成可注入的
`source`，就是将来接上设置表时**不必改调用点**的那条缝。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from muad_common import SharedSettings
from muad_contracts.platform_settings import (
    CompactionConfigError,
    CompactionSettings,
    merge_compaction_payload,
    parse_compaction_settings,
)

#: 平台级设置源：返回一份（部分）`compaction` 覆盖。
ContextSettingsSource = Callable[[], Mapping[str, Any]]


def _no_platform_overrides() -> Mapping[str, Any]:
    return {}


class ContextSettingsCache:
    """设置源的短 TTL 缓存 + 与 Agent 覆盖的合并入口。"""

    def __init__(
        self,
        *,
        ttl_sec: float,
        source: ContextSettingsSource | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_sec = max(0.0, float(ttl_sec))
        self._source = source or _no_platform_overrides
        self._clock = clock
        self._cached: Mapping[str, Any] | None = None
        self._expires_at = 0.0

    def platform_overrides(self) -> Mapping[str, Any]:
        now = self._clock()
        if self._cached is None or now >= self._expires_at:
            self._cached = dict(self._source())
            self._expires_at = now + self._ttl_sec
        return self._cached

    def invalidate(self) -> None:
        self._cached = None

    def resolve(self, agent_runtime_config: Mapping[str, Any] | None) -> CompactionSettings:
        """合并出这个 Agent 的完整压缩配置；非法配置显式报错。"""
        budget = (agent_runtime_config or {}).get("budget") or {}
        if not isinstance(budget, Mapping):
            raise CompactionConfigError("runtime_config.budget 必须是对象")
        payload = merge_compaction_payload(
            base=self.platform_overrides(), override=budget.get("compaction")
        )
        return parse_compaction_settings(payload)


_default_cache: ContextSettingsCache | None = None


def default_settings_cache() -> ContextSettingsCache:
    """进程级默认缓存（生产路径用）；TTL 来自 `CONTEXT_SETTINGS_CACHE_TTL_SEC`。"""
    global _default_cache
    if _default_cache is None:
        _default_cache = ContextSettingsCache(ttl_sec=SharedSettings().context_settings_cache_ttl_sec)
    return _default_cache


def resolve_compaction_settings(
    agent_runtime_config: Mapping[str, Any] | None,
    *,
    cache: ContextSettingsCache | None = None,
) -> CompactionSettings:
    return (cache or default_settings_cache()).resolve(agent_runtime_config)


__all__ = [
    "ContextSettingsCache",
    "ContextSettingsSource",
    "default_settings_cache",
    "resolve_compaction_settings",
]
