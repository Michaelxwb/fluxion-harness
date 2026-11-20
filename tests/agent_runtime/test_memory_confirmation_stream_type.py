"""确认回执的流类型：Web 面（含外部平台）由历史刷新展示，IM 渠道走 `message.delta`。

回归点：`_stream_type_for` 曾写死 `channel == "WEBCHAT"`，把 MSSWWEB / 外部平台（MSSPWEB）误判
成 IM 文本渠道，导致确认回执作为一条 `message.delta` 推进流里、与正文重复展示。修复后按
`is_web_surface_channel`（内建基线 ∪ 已启用外部平台通道）判定，新增平台无需改代码。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from muad_agent_runtime.application.memory_confirmation_resume import _stream_type_for
from muad_common import reset_settings_cache


@pytest.fixture(autouse=True)
def _clean_platform_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("EXTERNAL_ASSERTION_PLATFORMS_JSON", raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


def _run(channel_type: str) -> SimpleNamespace:
    return SimpleNamespace(channel_json={"type": channel_type})


@pytest.mark.parametrize("channel", ["WEBCHAT", "MSSWWEB"])
def test_builtin_web_surface_uses_history_refresh(channel: str) -> None:
    """内建 Web 面（含 MSSWWEB）回执不走进度流。"""
    assert _stream_type_for(_run(channel)) is None


def test_im_channel_uses_message_delta() -> None:
    """企微等文本渠道没有历史刷新入口，回执必须作为 message.delta 投递。"""
    assert _stream_type_for(_run("WECOM")) == "message.delta"


def test_configured_external_platform_uses_history_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置了外部平台后，其通道也按 Web 面处理（历史刷新），不再误判成 message.delta。"""
    monkeypatch.setenv(
        "EXTERNAL_ASSERTION_PLATFORMS_JSON",
        json.dumps({"mssp": {"channel": "MSSPWEB"}}),
    )
    reset_settings_cache()

    assert _stream_type_for(_run("MSSPWEB")) is None
    # 未配置的通道仍按 IM 处理（未知不静默当 Web）。
    assert _stream_type_for(_run("FOOWEB")) == "message.delta"
