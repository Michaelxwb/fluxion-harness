"""Connector 批次设置读取：缓存、降级与默认值。"""

from __future__ import annotations

from typing import Any

import pytest
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_im_gateway.application.ports import PlatformSettingsSnapshot

from muad_im_connector.platform_settings import (
    ConnectorPlatformSettings,
    default_batch_settings,
)


class _FakeClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls = 0

    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        assert isinstance(response, PlatformSettingsSnapshot)
        return response


def _snapshot(**im: Any) -> PlatformSettingsSnapshot:
    return PlatformSettingsSnapshot(revision=1, settings={"im": im})


async def test_defaults_and_tenant_values() -> None:
    assert default_batch_settings().quiet_ms == 300
    assert default_batch_settings().max_wait_ms == 5000
    assert default_batch_settings().max_attachments == 5

    client = _FakeClient([_snapshot(inbound_batch_quiet_ms=500, inbound_batch_max_wait_ms=6000)])
    settings = ConnectorPlatformSettings(client)
    resolved = await settings.batch("tenant-a")
    assert (resolved.quiet_ms, resolved.max_wait_ms, resolved.max_attachments) == (500, 6000, 5)


async def test_cache_hit_and_expiry() -> None:
    now = 0.0
    client = _FakeClient([_snapshot(inbound_batch_max_attachments=3)])
    settings = ConnectorPlatformSettings(client, ttl_sec=10, clock=lambda: now)

    first = await settings.batch("tenant-a")
    now = 5.0
    second = await settings.batch("tenant-a")
    assert first is second
    assert client.calls == 1

    now = 11.0
    client.responses.append(_snapshot(inbound_batch_max_attachments=4))
    third = await settings.batch("tenant-a")
    assert third.max_attachments == 4
    assert client.calls == 2


async def test_failure_uses_last_known_then_defaults(caplog: pytest.LogCaptureFixture) -> None:
    client = _FakeClient(
        [
            _snapshot(inbound_batch_max_attachments=2),
            AppError(ErrorCode.COMMON_INTERNAL_ERROR),
        ]
    )
    now = 0.0
    settings = ConnectorPlatformSettings(client, ttl_sec=1, clock=lambda: now)

    assert (await settings.batch("tenant-a")).max_attachments == 2
    now = 2.0
    assert (await settings.batch("tenant-a")).max_attachments == 2
    assert "using=last-known" in caplog.text

    fresh = ConnectorPlatformSettings(
        _FakeClient([AppError(ErrorCode.COMMON_INTERNAL_ERROR)])
    )
    assert (await fresh.batch("tenant-b")) == default_batch_settings()
    assert "using=defaults" in caplog.text


async def test_invalid_settings_fall_back(caplog: pytest.LogCaptureFixture) -> None:
    client = _FakeClient([_snapshot(inbound_batch_quiet_ms=9000)])
    settings = ConnectorPlatformSettings(client)
    assert (await settings.batch("tenant-a")) == default_batch_settings()
    assert "using=defaults" in caplog.text
