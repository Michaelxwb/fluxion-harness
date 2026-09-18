from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "apps/console-platform/frontend/src/components/common/DetailSideSheet.tsx"
COMMON = ROOT / "apps/console-platform/frontend/src/components/common"
USER_DETAIL = ROOT / "apps/console-platform/frontend/src/modules/user-identity/UserDetailTabs.tsx"
MODEL_DETAIL = ROOT / "apps/console-platform/frontend/src/modules/model-management/ModelDetailSideSheet.tsx"
FORM_MODAL = COMMON / "FormModal.tsx"


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


def test_detail_layout_components_exist_and_are_shared() -> None:
    grid = (COMMON / "DetailGrid.tsx").read_text(encoding="utf-8")
    cards = (COMMON / "MetricCards.tsx").read_text(encoding="utf-8")
    assert "export function DetailGrid" in grid
    assert "export function MetricCards" in cards

    user = USER_DETAIL.read_text(encoding="utf-8")
    model = MODEL_DETAIL.read_text(encoding="utf-8")
    for source in (user, model):
        assert "DetailGrid" in source, "详情基本信息必须使用 DetailGrid 双列栅格"
        assert "detail-section-title" in source, "详情必须带分区标题"
    assert "MetricCards" in user, "用户详情必须渲染当前授权概览卡片"


def test_user_detail_tabs_share_sectioned_layout() -> None:
    source = USER_DETAIL.read_text(encoding="utf-8")
    assert source.count("detail-section-title") >= 5, "每个 Tab 都必须有分区标题"
    for key in ("user.agents.hint", "user.identities.hint", "user.memory.hint"):
        assert key in source, f"Tab 说明文案缺失: {key}"
    for column in ("granted_at", "source_type", "update_time", "user_status"):
        assert column in source, f"与交互稿不一致的列缺失: {column}"


def test_model_detail_has_hint_banner_and_status_tags() -> None:
    source = MODEL_DETAIL.read_text(encoding="utf-8")
    assert "model.detail.hint" in source
    assert "Banner" in source
    for key in ("model.test.status.available", "model.test.status.failed", "model.test.status.untested"):
        assert key in source
    assert "r${model.revision}" in source


def test_agent_grant_uses_form_modal_with_hint() -> None:
    user = USER_DETAIL.read_text(encoding="utf-8")
    modal = FORM_MODAL.read_text(encoding="utf-8")
    assert "FormModal" in user, "授权 Agent 必须以 FormModal 承载选择"
    assert "IconPlus" in user, "主操作按钮必须带 \u002b 图标"
    assert "user.agents.grantHint" in user
    assert "user.agents.allGranted" in user
    assert "availableAgents" in user, "弹窗只允许选择未授权的 Agent"
    assert "okButtonProps" in modal, "FormModal 必须支持禁用确认按钮"
