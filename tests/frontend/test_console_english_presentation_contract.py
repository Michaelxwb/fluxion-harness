"""English heading conventions and adjustable navigation presentation."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "apps/console-platform/frontend/src"


def test_english_column_and_tab_words_start_uppercase() -> None:
    translations = json.loads((SRC / "locales/en-US.json").read_text())
    for key, value in translations.items():
        if not {"columns", "tabs", "tab"}.intersection(key.split(".")):
            continue
        words = re.sub(r"\{\{.*?\}\}|\([^)]*\)", "", value)
        assert not re.search(r"(?<![A-Za-z'])[a-z][A-Za-z']*", words), (key, value)
    assert translations["skill.columns.currentVersion"] == "Current Version"
    assert translations["skill.columns.userScope"] == "User Scope"


def test_sidebar_resizes_without_changing_navigation_or_requests() -> None:
    source = (SRC / "layout/AppLayout.tsx").read_text()
    assert 'role="separator"' in source
    assert "setPointerCapture(event.pointerId)" in source
    assert "hasPointerCapture(event.pointerId)" in source
    assert "onKeyDown=" in source
    assert "collapsed ? 64 : sidebarWidth" in source
    assert "i18n.language.startsWith('en') ? 248 : 184" in source
    css = (SRC / "styles/app.css").read_text()
    assert ".app-sidebar-resize" in css and "cursor: col-resize" in css
    assert ".app-sider:not(.app-sider-collapsed) .semi-navigation-item-text" in css
