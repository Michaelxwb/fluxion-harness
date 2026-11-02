"""TASK-005 / S-06：用户经验页签的前端契约。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend/src"


def test_s06_user_tab_wires_shared_experience_views() -> None:
    tabs = (FRONTEND / "modules/user-identity/UserDetailTabs.tsx").read_text(encoding="utf-8")
    assert "ExperienceTab" in tabs
    assert "user.tabs.experience" in tabs

    tab = (FRONTEND / "modules/learning/ExperienceTab.tsx").read_text(encoding="utf-8")
    assert "listExperiences" in tab and "ExperienceList" in tab
    assert "ExperienceDetailSideSheet" in tab

    service = (FRONTEND / "modules/learning/services/learning.ts").read_text(encoding="utf-8")
    assert "from '../../../api/client'" in service
    assert "axios" not in service
    assert "Idempotency-Key" in service

    for locale in ("zh-CN", "en-US"):
        data = json.loads((FRONTEND / f"locales/{locale}.json").read_text(encoding="utf-8"))
        for key in (
            "user.tabs.experience",
            "learning.state.PROPOSED",
            "learning.state.RETIRED",
            "learning.actions.replay",
            "learning.actions.retire",
        ):
            assert key in data, f"{locale} missing {key}"
