from __future__ import annotations

from pathlib import Path

import pytest
from muad_api.catalog import MessageCatalog, MessageSpec
from muad_api.locale import normalize_locale

ROOT = Path(__file__).resolve().parents[1]
MESSAGES_FILE = ROOT / "config/api-messages.yaml"


@pytest.fixture()
def catalog() -> MessageCatalog:
    return MessageCatalog(MESSAGES_FILE, default_locale="zh-CN")


def test_normalize_locale_negotiation() -> None:
    assert normalize_locale("zh-CN") == "zh-CN"
    assert normalize_locale("zh-TW,zh;q=0.9") == "zh-CN"
    assert normalize_locale("en-US,en;q=0.8") == "en-US"
    assert normalize_locale(None) == "zh-CN"
    assert normalize_locale("fr-FR") == "zh-CN"


def test_catalog_messages_come_from_yaml(catalog: MessageCatalog) -> None:
    spec = catalog.spec("AGENT_NOT_FOUND")
    assert isinstance(spec, MessageSpec)
    assert spec.http_status == 404
    assert catalog.message("AGENT_NOT_FOUND", "zh-CN") == "Agent 不存在"
    assert catalog.message("AGENT_NOT_FOUND", "en-US") == "Agent not found"


def test_catalog_formats_message_args(catalog: MessageCatalog) -> None:
    catalog._specs["TEST_ARG_CODE"] = MessageSpec(
        code="TEST_ARG_CODE",
        http_status=400,
        messages={"zh-CN": "字段 {field} 无效", "en-US": "Invalid field {field}"},
    )
    assert catalog.message("TEST_ARG_CODE", "zh-CN", {"field": "name"}) == "字段 name 无效"
    assert catalog.message("TEST_ARG_CODE", "en-US", {"field": "name"}) == "Invalid field name"


def test_catalog_requires_both_locales(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(
        "codes:\n  COMMON_INTERNAL_ERROR:\n    http_status: 500\n    messages:\n      zh-CN: 系统内部错误\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing en-US message"):
        MessageCatalog(invalid)


def test_unknown_code_falls_back_without_silence(catalog: MessageCatalog) -> None:
    assert catalog.has("NOPE") is False
    spec = catalog.spec("NOPE")
    assert spec.code == "COMMON_INTERNAL_ERROR"
    assert spec.http_status == 500
