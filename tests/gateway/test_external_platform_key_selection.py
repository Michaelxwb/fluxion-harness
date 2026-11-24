"""外部断言平台装配摘要的解析：多平台各配各的通道/断言头，坏配置显式失败不静默。"""

from __future__ import annotations

from muad_common import SharedSettings
from muad_im_gateway.channels.registry import (
    DEFAULT_ASSERTION_HEADER,
    DEFAULT_EXTERNAL_CHANNEL,
    parse_external_platform_surfaces,
)


def _settings(raw: str) -> SharedSettings:
    return SharedSettings(external_assertion_platforms_json=raw)


def test_single_platform_uses_default_channel_and_header() -> None:
    surfaces = parse_external_platform_surfaces(_settings('{"mssw": {"public_key": "x"}}'))
    assert surfaces == {"mssw": (DEFAULT_EXTERNAL_CHANNEL, DEFAULT_ASSERTION_HEADER)}


def test_platform_can_override_channel_and_header() -> None:
    raw = '{"acme": {"channel": "ACMEWEB", "assertion_header": "acme-token"}}'
    surfaces = parse_external_platform_surfaces(_settings(raw))
    assert surfaces == {"acme": ("ACMEWEB", "acme-token")}


def test_multiple_platforms_each_get_their_own_surface() -> None:
    """多平台现在是被支持的：各平台各自一条 surface（不再「多配即失败」）。"""
    raw = '{"mssw": {}, "acme": {"channel": "ACMEWEB"}}'
    surfaces = parse_external_platform_surfaces(_settings(raw))
    assert surfaces == {
        "mssw": (DEFAULT_EXTERNAL_CHANNEL, DEFAULT_ASSERTION_HEADER),
        "acme": ("ACMEWEB", DEFAULT_ASSERTION_HEADER),
    }


def test_channel_collision_keeps_first_and_drops_later() -> None:
    """一平台一通道：两平台配同一 channel 会在入口层冲突，后者被丢弃（不静默合并）。"""
    raw = '{"mssw": {"channel": "SHAREDWEB"}, "acme": {"channel": "SHAREDWEB"}}'
    surfaces = parse_external_platform_surfaces(_settings(raw))
    assert surfaces == {"mssw": ("SHAREDWEB", DEFAULT_ASSERTION_HEADER)}


def test_invalid_channel_name_is_skipped_not_loaded() -> None:
    """通道名不合规（RULE-im-013）的平台被跳过：否则 nginx 的 `[a-z0-9]+web` 匹配不到 →
    运行期静默 404。Gateway 保持"坏平台只影响自己"，不连带整表失败。"""
    raw = '{"bad": {"channel": "MSSPWEBUAT"}, "good": {"channel": "MSSPWEB"}}'
    surfaces = parse_external_platform_surfaces(_settings(raw))
    assert surfaces == {"good": ("MSSPWEB", DEFAULT_ASSERTION_HEADER)}


def test_empty_config_returns_empty() -> None:
    assert parse_external_platform_surfaces(_settings("")) == {}


def test_malformed_json_returns_empty() -> None:
    assert parse_external_platform_surfaces(_settings("{not json")) == {}


def test_non_object_returns_empty() -> None:
    assert parse_external_platform_surfaces(_settings("[]")) == {}


def test_long_numeric_platform_id_is_not_corrupted_by_float_parsing() -> None:
    """平台 id 可能是超长数字；按 float 解析会丢精度，与 Console 侧键不一致。"""
    big = "12345678901234567890"
    surfaces = parse_external_platform_surfaces(_settings(f'{{"{big}": {{}}}}'))
    assert list(surfaces) == [big]
