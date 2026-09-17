from pathlib import Path

from muad_api.catalog import MessageCatalog

ROOT = Path(__file__).resolve().parents[1]


def test_error_code_maps_zh_and_en():
    catalog = MessageCatalog(ROOT / 'config/api-messages.yaml')
    assert catalog.message('AGENT_NOT_FOUND', 'zh-CN') == 'Agent 不存在'
    assert catalog.message('AGENT_NOT_FOUND', 'en-US') == 'Agent not found'
    assert catalog.spec('AGENT_NOT_FOUND').http_status == 404


def test_unknown_code_maps_internal_error():
    catalog = MessageCatalog(ROOT / 'config/api-messages.yaml')
    assert catalog.message('UNKNOWN_CODE', 'en-US') == 'Internal server error'
