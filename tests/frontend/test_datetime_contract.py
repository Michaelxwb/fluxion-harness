from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "apps/console-platform/frontend/src/components/common/DateTimeText.tsx"


def test_datetime_text_uses_the_console_format() -> None:
    source = COMPONENT.read_text(encoding="utf-8")
    assert "DATE_TIME_FORMAT = 'YYYY-MM-DD HH:mm:ss'" in source
    assert "padStart(2, '0')" in source
    assert "export function DateTimeText" in source


def test_datetime_text_is_not_hardcoded_in_pages() -> None:
    pages = ROOT / "apps/console-platform/frontend/src/pages"
    offenders = [
        path.name
        for path in sorted(pages.glob("*.tsx"))
        if "toLocaleString(" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
