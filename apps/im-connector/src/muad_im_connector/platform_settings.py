"""Connector 入站批次设置的读取与租户级缓存。

设置权威在 Console 平台设置快照（`im.inbound_batch_*`）；Connector 不能把窗口写死。
读取失败时按“最近一次成功快照 → 代码默认值”降级，并留下 WARNING——入站消息不能因为
设置面抖动被阻断。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from muad_api import AppError
from muad_contracts.platform_settings import (
    ImSettings,
    PlatformSettingsError,
    default_platform_settings,
    parse_platform_settings,
)
from muad_im_gateway.application.ports import PlatformSettingsClient

logger = logging.getLogger(__name__)

PLATFORM_SETTINGS_CACHE_TTL_SEC = 60.0


@dataclass(frozen=True, slots=True)
class ConnectorBatchSettings:
    quiet_ms: int
    max_wait_ms: int
    max_attachments: int

    @classmethod
    def from_im(cls, im: ImSettings) -> ConnectorBatchSettings:
        return cls(
            quiet_ms=im.inbound_batch_quiet_ms,
            max_wait_ms=im.inbound_batch_max_wait_ms,
            max_attachments=im.inbound_batch_max_attachments,
        )


def default_batch_settings() -> ConnectorBatchSettings:
    return ConnectorBatchSettings.from_im(default_platform_settings().im)


class ConnectorPlatformSettings:
    """按租户缓存平台设置；只投影批次需要的三个值。"""

    def __init__(
        self,
        client: PlatformSettingsClient,
        *,
        ttl_sec: float = PLATFORM_SETTINGS_CACHE_TTL_SEC,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_sec <= 0:
            raise ValueError("ttl_sec must be positive")
        self._client = client
        self._ttl_sec = ttl_sec
        self._clock = clock
        self._cache: dict[str, tuple[float, ConnectorBatchSettings]] = {}
        self._defaults = default_batch_settings()

    async def batch(self, tenant_id: str) -> ConnectorBatchSettings:
        cached = self._cache.get(tenant_id)
        if cached is not None and self._clock() - cached[0] < self._ttl_sec:
            return cached[1]
        try:
            snapshot = await self._client.fetch_snapshot(tenant_id=tenant_id)
            settings = parse_platform_settings(snapshot.settings).im
            resolved = ConnectorBatchSettings.from_im(settings)
        except (AppError, PlatformSettingsError) as exc:
            if cached is not None:
                logger.warning(
                    "connector_platform_settings_fallback tenant_id=%s error=%s using=last-known",
                    tenant_id,
                    type(exc).__name__,
                )
                return cached[1]
            logger.warning(
                "connector_platform_settings_fallback tenant_id=%s error=%s using=defaults",
                tenant_id,
                type(exc).__name__,
            )
            return self._defaults
        self._cache[tenant_id] = (self._clock(), resolved)
        return resolved

    async def aclose(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()


__all__ = [
    "ConnectorBatchSettings",
    "ConnectorPlatformSettings",
    "PLATFORM_SETTINGS_CACHE_TTL_SEC",
    "default_batch_settings",
]
