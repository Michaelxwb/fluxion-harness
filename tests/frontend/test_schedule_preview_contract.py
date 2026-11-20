"""Card contract: dispatch preflight facts are visible; authoring metadata is not frozen."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from muad_contracts import (
    ScheduleDispatchCheck,
    ScheduleDraftRequest,
    ScheduleJob,
    SchedulePreview,
)
from pydantic import ValidationError

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[2]
TYPES_TS = ROOT / "apps/console-platform/frontend/src/modules/webchat/types.ts"
DRAFT_CARD_TSX = ROOT / "apps/console-platform/frontend/src/modules/webchat/components/DraftCard.tsx"
LOCALES = ROOT / "apps/console-platform/frontend/src/locales"

DISPATCH_FIELDS = (
    "status",
    "checked_at",
    "occurrence_at",
    "invocation",
    "contract_version",
    "dependencies",
    "rendered",
    "snapshot_hash",
    "snapshot_bytes",
)


def _dispatch_check(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "status": "PASSED",
        "checked_at": "2026-11-18T01:00:00Z",
        "occurrence_at": "2026-11-19T01:00:00Z",
        "invocation": "argv",
        "contract_version": 1,
        "dependencies": ["SKILL:weekly-report"],
        "rendered": {
            "input": {},
            "argv": ["--company-id", "22921947"],
            "window": {"kind": "DAY", "offset": -1},
        },
        "snapshot_hash": "sha256:" + "a" * 64,
        "snapshot_bytes": 512,
    }
    data.update(overrides)
    return data


def _preview(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "mode": "SKILL",
        "payload": {
            "kind": "SKILL_PLAN",
            "skill_id": str(uuid.uuid4()),
            "input": {},
            "argv": ["--company-id", "22921947"],
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


def test_preview_round_trips_dispatch_check() -> None:
    preview = SchedulePreview.model_validate(
        _preview(dispatch_check=_dispatch_check())
    )

    assert preview.dispatch_check is not None
    assert preview.dispatch_check.status == "PASSED"
    assert preview.dispatch_check.invocation == "argv"
    assert preview.dispatch_check.rendered["argv"] == ["--company-id", "22921947"]
    dumped = preview.model_dump(mode="json")
    assert set(dumped["dispatch_check"]) == set(DISPATCH_FIELDS)


def test_dispatch_check_model_round_trips() -> None:
    fact = ScheduleDispatchCheck.model_validate(_dispatch_check())

    assert fact.contract_version == 1
    assert fact.snapshot_hash.startswith("sha256:")
    assert fact.dependencies == ["SKILL:weekly-report"]


def test_frozen_job_rejects_authoring_only_facts() -> None:
    job: dict[str, Any] = {
        "instruction": "每天 9 点生成昨日日报",
        "recurrence": {"kind": "CRON", "cron": "0 9 * * *", "timezone": "Asia/Shanghai"},
        "payload": {
            "kind": "SKILL_PLAN",
            "skill_id": str(uuid.uuid4()),
            "input": {},
            "argv": ["--company-id", "22921947"],
            "window": {"kind": "DAY", "offset": -1},
        },
        "session_target": "isolated",
        "delivery": {"intent": "NONE"},
    }
    assert ScheduleJob.model_validate(job).payload.kind == "SKILL_PLAN"

    with pytest.raises(ValidationError):
        ScheduleJob.model_validate({**job, "dispatch_check": _dispatch_check()})
    with pytest.raises(ValidationError):
        ScheduleJob.model_validate({**job, "confidence": 0.9})

    draft_fields = set(ScheduleDraftRequest.model_fields)
    assert "dispatch_check" not in draft_fields
    assert "confidence" not in draft_fields
    assert "reason" not in draft_fields


def test_ts_preview_contract_matches_the_python_fact_block() -> None:
    source = TYPES_TS.read_text(encoding="utf-8")
    block = re.search(r"export interface ScheduleDispatchCheck \{(.*?)\n\}", source, re.S)
    assert block, "ScheduleDispatchCheck must exist in the WebChat types"
    for field in DISPATCH_FIELDS:
        assert re.search(rf"\b{field}\??:", block.group(1)), f"missing TS field {field}"
    assert re.search(r"dispatch_check\?: ScheduleDispatchCheck \| null;", source)


def test_draft_card_reads_dispatch_check_facts() -> None:
    source = DRAFT_CARD_TSX.read_text(encoding="utf-8")
    assert "preview.dispatch_check ?" in source
    assert "<DraftDispatchCheck check={preview.dispatch_check} />" in source
    assert 'data-testid="chat-draft-dispatch"' in source
    for token in (
        "check.status",
        "check.invocation",
        "check.dependencies",
        "check.occurrence_at",
        "preview.budget_source",
    ):
        assert token in source, f"DraftCard must display {token}"

    for locale in ("zh-CN", "en-US"):
        data = json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
        for key in (
            "chat.draft.dispatch.title",
            "chat.draft.dispatch.status",
            "chat.draft.dispatch.invocation",
            "chat.draft.dispatch.dependencies",
            "chat.draft.dispatch.occurrence",
            "chat.draft.budgetSource",
            "chat.draft.budgetSource.PLATFORM",
            "chat.draft.budgetSource.NONE",
        ):
            assert data.get(key), f"{locale} is missing {key}"
