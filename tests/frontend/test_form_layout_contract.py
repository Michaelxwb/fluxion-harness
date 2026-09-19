from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / "apps/console-platform/frontend/src/styles/app.css"


def test_form_grid_defines_two_columns_and_uniform_controls() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert ".form-grid," in css or ".form-grid {" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css
    assert ".form-grid .semi-select" in css and "width: 100%" in css
    assert ".form-section-title" in css and ".form-section-hint" in css


def test_platform_form_is_the_reference_layout() -> None:
    source = (
        ROOT / "apps/console-platform/frontend/src/modules/project-platform/ProjectPlatformForm.tsx"
    ).read_text(encoding="utf-8")
    assert 'className="form-grid"' in source
    assert "form-section-title" in source and "form-section-hint" in source
