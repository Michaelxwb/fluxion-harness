from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/project-platform"


def test_detail_sidesheet_actions_and_tabs() -> None:
    source = (MODULE / "PlatformDetailSideSheet.tsx").read_text(encoding="utf-8")
    assert "DetailSideSheet" in source
    assert "DetailGrid" in source
    assert "platform.actions.edit" in source
    assert "platform.actions.delete" in source
    assert "platform.actions.test" in source
    assert "ConfirmAction" in source
    assert "Tabs.TabPane" in source


def test_reconfigure_required_guides_to_credentials_tab() -> None:
    source = (MODULE / "PlatformDetailSideSheet.tsx").read_text(encoding="utf-8")
    assert "platform.detail.reconfigureRequired" in source
    assert "setActiveTab('credentials')" in source
    assert "Banner" in source
