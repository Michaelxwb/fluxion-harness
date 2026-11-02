"""TASK-006 / S-07：族页面与用户视图共用经验详情。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend/src"


def test_family_page_reuses_shared_experience_detail_and_routes() -> None:
    page = (FRONTEND / "modules/learning/LearningPage.tsx").read_text(encoding="utf-8")
    assert "listFamilies" in page and "FamilyDetailSideSheet" in page
    assert "ExperienceDetailSideSheet" in page
    assert "set('experience'" in page and "set('family'" in page

    family = (FRONTEND / "modules/learning/FamilyDetailSideSheet.tsx").read_text(encoding="utf-8")
    assert "ExperienceList" in family
    assert "getFamily" in family

    menu = (FRONTEND / "config/menu.ts").read_text(encoding="utf-8")
    assert "'/learning'" in menu and "adminOnly: true" in menu
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    assert 'path="learning"' in app and "RequireRole" in app

    for locale in ("zh-CN", "en-US"):
        data = json.loads((FRONTEND / f"locales/{locale}.json").read_text(encoding="utf-8"))
        assert "nav.learning" in data and "learning.family.pageTitle" in data
