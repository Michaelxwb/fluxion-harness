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


def test_detail_has_error_state_and_localized_fields() -> None:
    source = (MODULE / "PlatformDetailSideSheet.tsx").read_text(encoding="utf-8")
    assert "ErrorState" in source
    assert "common.status.enabled" in source and "common.status.disabled" in source
    assert "JSON.stringify" not in source, "访问配置不得展示原始 JSON"
    assert "PlatformCredentialTab" in source and "onChanged" in source


def test_credentials_pane_is_admin_only() -> None:
    """凭据入口仅 ADMIN（设计 §2.4「前端隐藏 + 后端 403 兜底」）。

    后端门控见 tests/console_auth/test_rbac.py::test_builder_cannot_access_credentials_routes；
    本用例只钉前端这一半：非 ADMIN 不得渲染该 pane，且自动切换也要受同一门控约束。
    """
    source = (MODULE / "PlatformDetailSideSheet.tsx").read_text(encoding="utf-8")
    assert "useAuth" in source
    assert "account?.role === 'ADMIN'" in source
    assert "isAdmin && (" in source
    assert source.index("isAdmin && (") < source.index('itemKey="credentials"'), (
        "凭据 TabPane 必须包在 isAdmin 门控内"
    )
    assert "props.reconfigureRequired && isAdmin" in source, (
        "自动切换到凭据 Tab 也必须受 ADMIN 门控约束（否则会落到不存在的 pane）"
    )
