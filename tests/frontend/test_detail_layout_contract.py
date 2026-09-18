from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMON = ROOT / "apps/console-platform/frontend/src/components/common"
USER_DETAIL = ROOT / "apps/console-platform/frontend/src/modules/user-identity/UserDetailTabs.tsx"
MODEL_DETAIL = ROOT / "apps/console-platform/frontend/src/modules/model-management/ModelDetailSideSheet.tsx"


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


def test_model_detail_has_hint_banner_and_status_tags() -> None:
    source = MODEL_DETAIL.read_text(encoding="utf-8")
    assert "model.detail.hint" in source
    assert "Banner" in source
    for key in ("model.test.status.available", "model.test.status.failed", "model.test.status.untested"):
        assert key in source
    assert "r${model.revision}" in source
