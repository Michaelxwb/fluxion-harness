"""TASK-014 · 卡片契约：试跑事实可见，authoring-only 元数据不进冻结 Job。

契约两端必须同形：

- Python `SchedulePreview` / `ScheduleTrialFact`（Worker 与 Runtime 共用）允许一个可选
  `trial` 块，只装真实执行 id/状态/结果/产物/错误与修复建议；
- WebChat 的 `SchedulePreview` TS 类型与 `DraftCard` 读同一组字段；
- `ScheduleJob`（确认后冻结的 Revision 配置）**拒绝** `trial`/authoring 元数据：`extra=forbid`。

这不是渲染快照测试：它证明事实形状、字段名与词条两端一致；真实渲染由前端构建产物在
浏览器/E2E 验证。
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from muad_contracts import (
    ScheduleDraftRequest,
    ScheduleJob,
    SchedulePreview,
    ScheduleTrialFact,
    TaskArtifactRef,
)
from pydantic import ValidationError

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[2]
TYPES_TS = ROOT / "apps/console-platform/frontend/src/modules/webchat/types.ts"
DRAFT_CARD_TSX = ROOT / "apps/console-platform/frontend/src/modules/webchat/components/DraftCard.tsx"
LOCALES = ROOT / "apps/console-platform/frontend/src/locales"

TRIAL_FIELDS = (
    "execution_id",
    "status",
    "error_code",
    "repair",
    "repair_hint",
    "result_summary",
    "artifacts",
    "started_at",
    "finished_at",
)


def _artifact() -> TaskArtifactRef:
    return TaskArtifactRef(
        artifact_id=uuid.uuid4(),
        name="report.txt",
        media_type="text/plain",
        size=12,
        checksum="sha256:" + "a" * 64,
        version="1",
    )


def _preview(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "mode": "SKILL",
        "payload": {
            "kind": "SKILL_PLAN",
            "skill_id": str(uuid.uuid4()),
            "input": {"company_id": "22921947"},
            "argv": [],
            "window": {"kind": "DAY", "offset": -1},
        },
        "session_target": "isolated",
        "what": "生成昨日日报",
        "when": "CRON 0 9 * * * (Asia/Shanghai)",
        "first_fire_at": "2026-11-19T01:00:00Z",
        "delivery": "NONE",
        "tools_allow": [],
        "budget_source": "NONE",
        "dependencies": ["SKILL:weekly-report"],
        "budget": {},
        "diff": [],
        "confidence": 0.9,
        "reason": "fixed skill",
    }
    data.update(overrides)
    return data


def test_preview_round_trips_real_trial_facts() -> None:
    trial = {
        "execution_id": str(uuid.uuid4()),
        "status": "COMPLETED",
        "error_code": None,
        "repair": None,
        "repair_hint": None,
        "result_summary": "昨日日报已生成",
        "artifacts": [_artifact().model_dump(mode="json")],
        "started_at": "2026-11-18T01:00:00Z",
        "finished_at": "2026-11-18T01:00:10Z",
    }
    preview = SchedulePreview.model_validate(_preview(trial=trial))
    assert preview.trial is not None
    assert preview.trial.execution_id == uuid.UUID(trial["execution_id"])
    assert preview.trial.status.value == "COMPLETED"
    assert preview.trial.result_summary == "昨日日报已生成"
    assert preview.trial.artifacts[0].name == "report.txt"
    dumped = preview.model_dump(mode="json")
    assert set(dumped["trial"]) == set(TRIAL_FIELDS)


def test_failed_trial_carries_error_and_plain_repair() -> None:
    trial = {
        "execution_id": str(uuid.uuid4()),
        "status": "FAILED",
        "error_code": "SKILL_EXECUTION_FAILED",
        "repair": "CHECK_SKILL_INPUT_OR_SCRIPT",
        "repair_hint": "检查 Skill 入参与脚本本身的报错，修正后再次试跑。",
        "result_summary": None,
        "artifacts": [],
        "started_at": "2026-11-18T01:00:00Z",
        "finished_at": "2026-11-18T01:00:03Z",
    }
    fact = ScheduleTrialFact.model_validate(trial)
    assert fact.error_code == "SKILL_EXECUTION_FAILED"
    assert fact.repair == "CHECK_SKILL_INPUT_OR_SCRIPT"
    assert fact.repair_hint


def test_frozen_job_rejects_authoring_only_facts() -> None:
    job: dict[str, Any] = {
        "instruction": "每天 9 点生成昨日日报",
        "recurrence": {"kind": "CRON", "cron": "0 9 * * *", "timezone": "Asia/Shanghai"},
        "payload": {
            "kind": "SKILL_PLAN",
            "skill_id": str(uuid.uuid4()),
            "input": {"company_id": "22921947"},
            "argv": [],
            "window": {"kind": "DAY", "offset": -1},
        },
        "session_target": "isolated",
        "delivery": {"intent": "NONE"},
    }
    assert ScheduleJob.model_validate(job).payload.kind == "SKILL_PLAN"

    with pytest.raises(ValidationError):
        ScheduleJob.model_validate({**job, "trial": None})
    with pytest.raises(ValidationError):
        ScheduleJob.model_validate({**job, "confidence": 0.9})

    draft_fields = set(ScheduleDraftRequest.model_fields)
    assert "trial" not in draft_fields
    assert "confidence" not in draft_fields
    assert "reason" not in draft_fields


def test_ts_preview_contract_matches_the_python_fact_block() -> None:
    source = TYPES_TS.read_text(encoding="utf-8")
    block = re.search(r"export interface ScheduleTrialFact \{(.*?)\n\}", source, re.S)
    assert block, "ScheduleTrialFact must exist in the WebChat types"
    for field in TRIAL_FIELDS:
        assert re.search(rf"\b{field}\??:", block.group(1)), f"missing TS trial field {field}"
    assert re.search(r"trial\?: ScheduleTrialFact \| null;", source)
    assert "ScheduleTrialArtifact" in source


def test_draft_card_reads_every_trial_fact() -> None:
    source = DRAFT_CARD_TSX.read_text(encoding="utf-8")
    assert "preview.trial ? <DraftTrial trial={preview.trial} /> : null" in source
    assert 'data-testid="chat-draft-trial"' in source
    for token in (
        "trial.execution_id",
        "trial.status",
        "trial.result_summary",
        "trial.artifacts",
        "trial.error_code",
        "trial.repair_hint",
        "preview.budget_source",
    ):
        assert token in source, f"DraftCard must display {token}"

    for locale in ("zh-CN", "en-US"):
        data = json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
        for key in (
            "chat.draft.trial.title",
            "chat.draft.trial.execution",
            "chat.draft.trial.status",
            "chat.draft.trial.result",
            "chat.draft.trial.artifact",
            "chat.draft.trial.failure",
            "chat.draft.trial.repair",
            "chat.draft.trial.running",
            "chat.draft.budgetSource",
            "chat.draft.budgetSource.PLATFORM",
            "chat.draft.budgetSource.NONE",
        ):
            assert data.get(key), f"{locale} is missing {key}"
