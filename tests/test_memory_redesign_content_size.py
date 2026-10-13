"""The migration and writer share one canonical fact-quota measurement."""

import pytest
from muad_contracts.canonical import NonCanonicalJsonError, canonical_json
from muad_contracts.memory import memory_content_bytes

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("quote", [None, "中文", 'line\n"quoted"'])
def test_memory_content_quota_counts_complete_canonical_json_and_quote(quote):
    content = {"value": "中文"}
    assert memory_content_bytes(content, quote) == (
        len(canonical_json(content).encode("utf-8")) + len(canonical_json(quote).encode("utf-8"))
    )
    assert memory_content_bytes(content, quote) > len("中文".encode())


def test_memory_content_quota_rejects_nonfinite_payload():
    with pytest.raises(NonCanonicalJsonError):
        memory_content_bytes({"value": float("nan")}, None)
