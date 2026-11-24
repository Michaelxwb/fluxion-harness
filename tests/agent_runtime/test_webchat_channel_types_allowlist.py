"""来源通道 allowlist 校验：内建通道 ∪ 已配置的外部平台通道。

回归点：Runtime 的 `_channel_types` 曾用封闭的 `BUILTIN_CHANNEL_NAMES`（不含 `MSSPWEB` 等外部
平台），而 Gateway 把「配置的全部外部平台通道」放进 `channel_types`（Web 面会话跨入口共享）。
两边语义不一致时，外部平台会话能写入却读不出：列表/详情请求被 Runtime 以 422 拒掉。本用例
锁定「白名单按同一份平台配置动态扩展」这一修复。
"""

from __future__ import annotations

import json

import pytest
from muad_agent_runtime.api.webchat_history import _channel_types
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import reset_settings_cache


@pytest.fixture
def platforms(monkeypatch: pytest.MonkeyPatch):
    """按需设置外部平台配置后重建进程设置快照；退出时还原并再次重建。"""

    def _install(payload: dict | None) -> None:
        if payload is None:
            monkeypatch.delenv("EXTERNAL_ASSERTION_PLATFORMS_JSON", raising=False)
        else:
            monkeypatch.setenv("EXTERNAL_ASSERTION_PLATFORMS_JSON", json.dumps(payload))
        reset_settings_cache()

    _install(None)
    yield _install
    reset_settings_cache()


def test_builtin_channels_always_accepted(platforms) -> None:
    """未配置外部平台时，只接受内建通道名（不放大既有白名单）。"""
    assert _channel_types("WEBCHAT,MSSWWEB") == ("WEBCHAT", "MSSWWEB")
    assert _channel_types("WECOM") == ("WECOM",)
    assert _channel_types(None) is None


def test_builtin_rejects_unknown_channel(platforms) -> None:
    """未配置的外部平台通道仍被拒绝（未知通道不静默放行）。"""
    with pytest.raises(AppError) as exc:
        _channel_types("WEBCHAT,MSSPWEB")
    assert exc.value.code == ErrorCode.COMMON_VALIDATION_ERROR


def test_configured_external_channel_is_accepted(platforms) -> None:
    """配置了外部平台后，其通道名并入白名单，可与内建通道一同传下。"""
    platforms({"mssw": {"channel": "MSSWWEB"}, "mssp": {"channel": "MSSPWEB"}})
    assert _channel_types("WEBCHAT,MSSWWEB,MSSPWEB") == ("WEBCHAT", "MSSWWEB", "MSSPWEB")
    assert _channel_types("MSSPWEB") == ("MSSPWEB",)

    # 拼写/大小写：统一大写后匹配。
    assert _channel_types("msspweb") == ("MSSPWEB",)

    # 仍是配置之外的通道 → 拒绝。
    with pytest.raises(AppError) as exc:
        _channel_types("MSSPWEB,FOOWEB")
    assert exc.value.code == ErrorCode.COMMON_VALIDATION_ERROR
