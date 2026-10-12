"""Memory errors are stable, localized, and never echo rejected content."""

from pathlib import Path

import pytest
from muad_api import ErrorCode
from muad_api.catalog import MessageCatalog

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "MEMORY_KEY_INVALID": 400, "MEMORY_VALUE_INVALID": 400,
    "MEMORY_CATEGORY_NOT_ALLOWED": 400, "MEMORY_QUERY_INVALID": 400,
    "MEMORY_LIMIT_INVALID": 400, "MEMORY_READ_FAILED": 503,
    "MEMORY_QUOTA_EXCEEDED": 409, "MEMORY_FORBIDDEN_CONTENT": 422,
    "MEMORY_NOT_FOUND": 404, "MEMORY_VERSION_CONFLICT": 409,
    "MEMORY_CONFIRMATION_EXPIRED": 410, "MEMORY_CONFIRMATION_INVALID": 400,
    "MEMORY_CONFIRMATION_PENDING": 409,
    "MEMORY_QUOTA_REDUCTION_REQUIRES_MAINTENANCE": 409,
    "MEMORY_RESULT_BUDGET_EXCEEDED": 422,
}
pytestmark = pytest.mark.contract


@pytest.mark.parametrize(("code", "http_status"), EXPECTED.items())
def test_memory_errors_have_exact_status_and_safe_both_locales(code, http_status):
    catalog = MessageCatalog(ROOT / "config/api-messages.yaml")
    assert code in ErrorCode.__members__
    assert catalog.has(code)
    assert catalog.spec(code).http_status == http_status
    for locale in ("zh-CN", "en-US"):
        message = catalog.message(code, locale, {"value": "PRIVATE-CANARY", "key": "PRIVATE-CANARY"})
        assert message.strip()
        assert "PRIVATE-CANARY" not in message
        assert "{" not in message and "}" not in message


def test_obsolete_source_and_prefix_errors_are_not_catalogued():
    catalog = MessageCatalog(ROOT / "config/api-messages.yaml")
    assert "MEMORY_SOURCE_TYPE_INVALID" not in catalog.codes()
    assert "MEMORY_PREFIX_INVALID" not in catalog.codes()
    assert "MEMORY_SOURCE_TYPE_INVALID" not in ErrorCode.__members__
    assert "MEMORY_PREFIX_INVALID" not in ErrorCode.__members__
