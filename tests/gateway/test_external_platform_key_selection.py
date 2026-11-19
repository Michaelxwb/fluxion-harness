"""外部断言平台键的选取：多平台必须显式失败，不能静默取首键。"""

from __future__ import annotations

from muad_common import SharedSettings
from muad_im_gateway.main import _single_external_platform_key


def _settings(raw: str) -> SharedSettings:
    return SharedSettings(external_assertion_platforms_json=raw)


def test_single_platform_returns_its_key() -> None:
    assert _single_external_platform_key(_settings('{"mssw": {"public_key": "x"}}')) == "mssw"


def test_empty_config_returns_none() -> None:
    assert _single_external_platform_key(_settings("")) is None


def test_malformed_json_returns_none() -> None:
    assert _single_external_platform_key(_settings("{not json")) is None


def test_multiple_platforms_fail_instead_of_picking_the_first() -> None:
    """静默取首键会让多配的平台不报错却永不生效，属于「改了没反应」的典型来源。"""
    raw = '{"mssw": {}, "other": {}}'
    assert _single_external_platform_key(_settings(raw)) is None


def test_long_numeric_platform_id_is_not_corrupted_by_float_parsing() -> None:
    """平台 id 可能是超长数字；按 float 解析会丢精度，与 Console 侧键不一致。"""
    big = "12345678901234567890"
    key = _single_external_platform_key(_settings(f'{{"{big}": {{}}}}'))
    assert key == big
