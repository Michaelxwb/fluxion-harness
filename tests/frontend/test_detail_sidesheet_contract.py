from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "apps/console-platform/frontend/src/components/common/DetailSideSheet.tsx"


def _source() -> str:
    return COMPONENT.read_text(encoding="utf-8")


def test_detail_sidesheet_exports_contract_props() -> None:
    source = _source()
    assert "export interface DetailSideSheetProps" in source
    for prop in ("visible", "title", "subtitle", "actions", "activeTab", "onCancel"):
        assert re.search(rf"\b{prop}\b", source), f"missing prop {prop}"


def test_detail_sidesheet_uses_semi_header_and_tabs() -> None:
    source = _source()
    assert "SideSheet" in source
    assert "Tabs" in source
    assert "onCancel" in source


def test_detail_sidesheet_renders_no_empty_action_area() -> None:
    source = _source()
    assert re.search(r"props\.actions\s*\?", source), "actions must be conditionally rendered"
