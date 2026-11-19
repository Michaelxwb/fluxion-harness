"""Golden intent set for `draft_schedule` (run-failure-reporting §10.4 / S-11 / S-15).

The model guides the choice through the tool description; the platform enforces the
deterministic half: payload kind is derived from the business fields (never a mode
picker), ambiguous intent becomes one business question, and an obviously wrong payload
becomes a structured field error before any Draft is sent to Worker.

These tests use the real tool handler and a recording Draft client, so they prove the
model-facing contract without needing a database or a second materials resolver.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.task_client import TaskSubmissionContext
from muad_agent_runtime.application.task_tools import (
    DRAFT_SCHEDULE_TOOL,
    BackgroundTaskToolSet,
)
from muad_contracts import (
    ResolvedAgent,
    ResolvedModel,
    ResolvedSkill,
    SkillExecutionMode,
    SkillSource,
    derive_schedule_mode,
    parse_schedule_payload,
)

pytestmark = pytest.mark.unit

TZ = "Asia/Shanghai"
SKILL_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")


class RecordingDraftClient:
    """Records each Draft body; returns a realistic Worker-shaped preview."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def list_delivery_targets(self, **kwargs: Any) -> dict[str, Any]:
        """Golden 用例不验证出站目标；显式返回空列表，让投递推导走默认来源路由。"""
        return {"items": []}

    async def create_schedule_draft(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        draft = cast(dict[str, Any], kwargs["draft"])
        payload = draft["payload"]
        mode = derive_schedule_mode(parse_schedule_payload(payload))
        tools = list(payload.get("tools_allow") or (["*"] if mode.value == "AGENTIC" else []))
        return {
            "draft_id": str(uuid.uuid4()),
            "draft_hash": "sha256:" + "d" * 64,
            "status": "PENDING",
            "preview": {
                "mode": mode.value,
                "payload": payload,
                "session_target": draft["session_target"],
                "what": payload.get("text") or payload.get("prompt") or draft["instruction"],
                "when": "CRON 0 9 * * * (Asia/Shanghai)",
                "first_fire_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "delivery": draft["delivery"]["intent"],
                "tools_allow": tools,
                "budget_source": "PLATFORM" if mode.value == "AGENTIC" else "NONE",
                "dependencies": [],
                "budget": {},
                "diff": [],
            },
            "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
        }


def _skill() -> ResolvedSkill:
    return ResolvedSkill(
        skill_id=SKILL_ID,
        artifact_id=uuid.uuid4(),
        key="daily-report",
        name="值守日报",
        description="Render the daily duty report",
        version="1.0.0",
        checksum="sha256:" + "c" * 64,
        storage_key="skills/daily-report.tar.gz",
        execution_mode=SkillExecutionMode.SYNC,
        frontmatter={"tools": ["report.render"]},
        source=SkillSource.ADMIN,
    )


def _context() -> TaskSubmissionContext:
    return TaskSubmissionContext(
        tenant_id="tenant-golden",
        actor_user_id=uuid.uuid4(),
        agent=ResolvedAgent(id=uuid.uuid4(), key="agent", revision=1, instructions="be brief"),
        model=ResolvedModel(
            id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini", base_url="http://model", params={}
        ),
        skills=(_skill(),),
        source_run_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        entry_channel="WEBCHAT",
    )


def _registry(client: RecordingDraftClient) -> ToolRegistry:
    registry = ToolRegistry()
    BackgroundTaskToolSet(
        client=client,  # type: ignore[arg-type] - the recording client implements the one method used
        context=_context(),
        skills=(_skill(),),
    ).register(registry)
    return registry


async def _invoke(
    registry: ToolRegistry, arguments: dict[str, Any], *, call_id: str | None = None
) -> dict[str, Any]:
    definition = registry.get(DRAFT_SCHEDULE_TOOL)
    assert definition is not None and definition.handler is not None
    payload = await definition.handler(
        arguments, call_id=call_id or f"call-{uuid.uuid4()}"
    )
    return cast(dict[str, Any], json.loads(payload))


def _args(payload: dict[str, Any] | None, **overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "instruction": "每天 9 点给客户 22921947 出昨天的值守日报",
        "timezone": TZ,
        "recurrence": {"kind": "CRON", "cron": "0 9 * * *"},
    }
    if payload is not None:
        arguments["payload"] = payload
    arguments.update(overrides)
    return arguments


def test_tool_description_carries_the_intent_tree_without_a_mode_choice() -> None:
    registry = _registry(RecordingDraftClient())
    definition = registry.get(DRAFT_SCHEDULE_TOOL)
    assert definition is not None
    description = definition.description
    assert "pure notification -> REMINDER" in description
    assert "fixed skill and fixed parameters -> SKILL_PLAN" in description
    assert "runtime judgment, multiple tools or context -> AGENT_TURN" in description
    assert "ONE business question" in description
    assert "mode" not in description.lower()

    payload_schema = definition.input_schema["properties"]["payload"]
    assert "mode" not in payload_schema["properties"]
    assert payload_schema["required"] == []
    assert "mode" not in definition.input_schema["properties"]


async def test_daily_report_golden_case_compiles_to_skill_plan() -> None:
    client = RecordingDraftClient()
    registry = _registry(client)
    result = await _invoke(
        registry,
        _args(
            {
                "skill_key": "daily-report",
                "input": {"company_id": "22921947"},
                "argv": ["--company-id", "22921947", "--date", "{{previous_day}}"],
                "window": {"kind": "DAY", "offset": -1},
            }
        ),
    )

    assert result["resolved_payload"]["kind"] == "SKILL_PLAN"
    assert result["resolved_payload"]["skill_id"] == str(SKILL_ID)
    assert result["preview"]["confidence"] == 1.0
    assert result["preview"]["reason"]
    assert result["budget_source"] == "NONE"
    assert result["session_target"] == "isolated"
    assert result["delivery"] == "NONE"
    assert result["tools_allow"] == []
    assert result["resolved_plan"]["payload"]["argv"][-1] == "{{previous_day}}"

    assert len(client.calls) == 1
    body = client.calls[0]["draft"]
    assert body["payload"]["kind"] == "SKILL_PLAN"
    assert body["payload"]["skill_id"] == str(SKILL_ID)


async def test_summary_golden_case_compiles_to_agent_turn() -> None:
    client = RecordingDraftClient()
    registry = _registry(client)
    result = await _invoke(
        registry,
        _args(
            {
                "prompt": "汇总 {{previous_day}} 的定时任务执行并指出阻塞项",
                "tools_allow": ["PLATFORM:list_tasks", "SKILL:daily-report:report.render"],
            }
        ),
    )

    assert result["resolved_payload"]["kind"] == "AGENT_TURN"
    assert result["budget_source"] == "PLATFORM"
    assert result["tools_allow"] == [
        "PLATFORM:list_tasks",
        f"SKILL:{SKILL_ID}:report.render",
    ]
    assert result["preview"]["budget_source"] == "PLATFORM"
    assert client.calls[0]["draft"]["payload"]["tools_allow"] == result["tools_allow"]


async def test_reminder_golden_case_compiles_to_reminder() -> None:
    client = RecordingDraftClient()
    registry = _registry(client)
    result = await _invoke(registry, _args({"text": "9 点开周会"}))

    assert result["resolved_payload"]["kind"] == "REMINDER"
    assert result["resolved_payload"]["text"] == "9 点开周会"
    assert result["budget_source"] == "NONE"
    assert result["delivery"] == "NONE"


async def test_ambiguous_golden_case_asks_one_business_question_without_a_draft() -> None:
    client = RecordingDraftClient()
    registry = _registry(client)
    result = await _invoke(
        registry,
        _args(
            {"prompt": "每天给我看看客户情况"},
            instruction="每天给我看看客户情况",
            confidence=0.2,
        ),
    )

    assert result["status"] == "NEEDS_CLARIFICATION"
    assert result["needs_clarification"] is True
    assert result["question"]
    assert result["confidence"] == 0.2
    for internal in ("SKILL_PLAN", "AGENT_TURN", "REMINDER", "mode", "模式"):
        assert internal not in result["question"]
    assert "ONE business question" in result["next_step"]
    assert client.calls == []


async def test_invalid_golden_case_returns_structured_field_error_without_a_draft() -> None:
    client = RecordingDraftClient()
    registry = _registry(client)
    result = await _invoke(registry, _args({"kind": "SKILL_PLAN"}))

    assert result["error"]["code"] == "SCHEDULE_PARAMETER_INCOMPLETE"
    fields = result["error"]["data"]["fields"]
    assert fields[0]["field"] == "payload.skill_key"
    assert fields[0]["reason"] == "MISSING"
    assert fields[0]["repair"] == "PROVIDE_SKILL_KEY"
    assert fields[0]["allowed_values"] == ["daily-report"]
    assert client.calls == []
