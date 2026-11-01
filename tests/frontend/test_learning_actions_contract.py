"""TASK-012 / S-15：止损/暂停/修订/反馈入口的前端契约。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend/src"


def test_s15_action_entries_require_reason_and_use_service_layer() -> None:
    run = (FRONTEND / "modules/learning/RunLearningActions.tsx").read_text(encoding="utf-8")
    assert "stopRun" in run and "feedbackRun" in run
    assert "reasonRequired" in run
    assert "axios" not in run

    detail = (FRONTEND / "modules/learning/ExperienceDetailSideSheet.tsx").read_text(
        encoding="utf-8"
    )
    assert "reviseExperience" in detail and "newRequestId" in detail

    family = (FRONTEND / "modules/learning/FamilyDetailSideSheet.tsx").read_text(encoding="utf-8")
    assert "familyControl" in family

    service = (FRONTEND / "modules/learning/services/learning.ts").read_text(encoding="utf-8")
    for path in ("/runs/${runId}/stop", "/runs/${runId}/feedback", "/control"):
        assert path in service

    for locale in ("zh-CN", "en-US"):
        data = json.loads((FRONTEND / f"locales/{locale}.json").read_text(encoding="utf-8"))
        for key in (
            "learning.actions.stop",
            "learning.actions.feedback",
            "learning.actions.pause",
            "learning.actions.revise",
        ):
            assert key in data, f"{locale} missing {key}"
