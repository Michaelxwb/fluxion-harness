"""Admin create only names a predicate; Console derives the kind from the taxonomy."""

from __future__ import annotations

import pytest
from muad_api import AppError
from muad_console_platform.application.memory_service import _create_candidate

pytestmark = pytest.mark.unit


def test_predicate_derives_kind_and_maps_text_value() -> None:
    candidate = _create_candidate(
        {"predicate": "report_format", "content": {"text": "PDF"}, "title": "报告格式"}
    )
    assert candidate["memory_kind"] == "PREFERENCE"
    assert candidate["content"] == {"value": "PDF"}


def test_note_predicate_keeps_text_content() -> None:
    candidate = _create_candidate(
        {"predicate": "general", "content": {"value": "随手记"}, "title": "笔记"}
    )
    assert candidate["memory_kind"] == "NOTE"
    assert candidate["content"] == {"text": "随手记"}


@pytest.mark.parametrize(
    "payload",
    [
        {"predicate": "unknown", "content": {"value": "x"}, "title": "t"},
        {"predicate": "report_format", "memory_kind": "NOTE", "content": {"value": "x"}, "title": "t"},
        {"content": {"value": "x"}, "title": "t"},
    ],
)
def test_unknown_or_mismatched_kind_is_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(AppError) as error:
        _create_candidate(payload)  # type: ignore[arg-type]
    assert error.value.code == "MEMORY_TAXONOMY_INVALID"
