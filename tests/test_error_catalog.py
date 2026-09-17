import re
from pathlib import Path

import pytest
from muad_api import AppError, ErrorCode, paginate
from muad_api.catalog import MessageCatalog

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / 'config/api-messages.yaml'
SOURCE_ROOTS = (ROOT / 'apps', ROOT / 'packages')
SOURCE_EXCLUDES = {'node_modules', '.venv', '__pycache__'}
APP_ERROR_LITERAL = re.compile(r'AppError\s*\(\s*["\'](?P<code>[A-Z][A-Z0-9_]*)["\']')
ERROR_CODE_MEMBER = re.compile(r'ErrorCode\.(?P<name>[A-Z][A-Z0-9_]*)')


def _source_files() -> list[Path]:
    return [
        path
        for root in SOURCE_ROOTS
        for path in root.rglob('*.py')
        if not SOURCE_EXCLUDES.intersection(path.parts)
    ]


def _used_source_codes() -> tuple[set[str], set[str]]:
    literals: set[str] = set()
    members: set[str] = set()
    for path in _source_files():
        text = path.read_text(encoding='utf-8')
        literals.update(match.group('code') for match in APP_ERROR_LITERAL.finditer(text))
        members.update(match.group('name') for match in ERROR_CODE_MEMBER.finditer(text))
    return literals, members


def _catalog() -> MessageCatalog:
    return MessageCatalog(CATALOG_PATH)


def _write_catalog(tmp_path: Path, body: str) -> Path:
    path = tmp_path / 'api-messages.yaml'
    path.write_text(body, encoding='utf-8')
    return path


def test_catalog_codes_match_error_code_enum_bidirectionally():
    catalog = _catalog()
    assert catalog.codes() - {'0'} == {code.value for code in ErrorCode}
    assert '0' in catalog.codes()
    assert catalog.has('RUN_BUSY')
    assert catalog.has('SKILL_PACKAGE_INVALID')
    assert not catalog.has('DOES_NOT_EXIST')


def test_source_error_codes_are_registered():
    catalog = _catalog()
    literals, members = _used_source_codes()
    unknown_members = members - set(ErrorCode.__members__)
    assert not unknown_members, f'unknown ErrorCode members used in source: {sorted(unknown_members)}'
    used = literals | {str(ErrorCode[name]) for name in members}
    assert 'COMMON_INTERNAL_ERROR' in used
    missing = used - catalog.codes()
    assert not missing, f'error codes used in source but absent from catalog: {sorted(missing)}'


def test_every_catalog_code_has_non_empty_messages():
    catalog = _catalog()
    for code in catalog.codes():
        spec = catalog.spec(code)
        assert 100 <= spec.http_status <= 599
        for locale in ('zh-CN', 'en-US'):
            assert spec.messages[locale].strip()
    assert catalog.has('COMMON_INTERNAL_ERROR')


def test_catalog_rejects_invalid_http_status(tmp_path):
    path = _write_catalog(
        tmp_path,
        'codes:\n'
        '  COMMON_INTERNAL_ERROR:\n'
        '    http_status: 42\n'
        '    messages:\n'
        '      zh-CN: 系统内部错误\n'
        '      en-US: Internal server error\n',
    )
    with pytest.raises(ValueError):
        MessageCatalog(path)


def test_catalog_rejects_missing_locale(tmp_path):
    path = _write_catalog(
        tmp_path,
        'codes:\n'
        '  COMMON_INTERNAL_ERROR:\n'
        '    http_status: 500\n'
        '    messages:\n'
        '      zh-CN: 系统内部错误\n',
    )
    with pytest.raises(ValueError):
        MessageCatalog(path)


def test_catalog_requires_internal_error_fallback(tmp_path):
    path = _write_catalog(
        tmp_path,
        'codes:\n'
        '  COMMON_BAD_REQUEST:\n'
        '    http_status: 400\n'
        '    messages:\n'
        '      zh-CN: 请求参数错误\n'
        '      en-US: Bad request\n',
    )
    with pytest.raises(ValueError):
        MessageCatalog(path)


def test_paginate_returns_default_page_shape():
    result = paginate(items=[1, 2, 3], total=3)
    assert result == {'items': [1, 2, 3], 'page': 1, 'page_size': 20, 'total': 3}


def test_paginate_rejects_page_zero():
    with pytest.raises(AppError) as exc_info:
        paginate(items=[], page=0, total=0)
    assert exc_info.value.code == ErrorCode.COMMON_VALIDATION_ERROR


def test_paginate_rejects_page_size_above_maximum():
    with pytest.raises(AppError) as exc_info:
        paginate(items=[], page_size=101, total=0)
    assert exc_info.value.code == ErrorCode.COMMON_VALIDATION_ERROR
