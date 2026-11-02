"""B-01: 任务族判定纯函数边界（执行层切换与 Run 族判定同源）。"""

from __future__ import annotations

from datetime import UTC, datetime

from muad_agent_core.memory.rank import (
    MemoryRecord,
    select_injection,
)
from muad_agent_runtime.application.task_family import derive_task_family, skill_key_for_call
from muad_contracts.memory import FactContent, MemoryAuthor, MemoryCategory


def _call(call_id: str, name: str, key: str | None) -> dict:
    return {"id": call_id, "name": name, "arguments": {"skill_key": key} if key else {}}


def test_b01_first_successful_skill_wins_and_failed_calls_do_not_switch() -> None:
    events = [
        {"event_type": "ASSISTANT_TURN", "payload": {"tool_calls": [
            _call("c1", "execute_skill", "first"),
            _call("c2", "execute_skill", "second"),
        ]}},
        {
            "event_type": "TOOL_CALL",
            "payload": {"tool_call_id": "c1", "tool_name": "execute_skill", "status": "ERROR"},
        },
        {
            "event_type": "TOOL_CALL",
            "payload": {"tool_call_id": "c2", "tool_name": "execute_skill", "status": "OK"},
        },
    ]
    assert derive_task_family(events, agent_id="a1") == "skill:second"
    assert skill_key_for_call("execute_skill", {"skill_key": " x "}) == "x"
    assert skill_key_for_call("run_skill_script", {"skill_key": "y"}) == "y"
    assert skill_key_for_call("other", {"skill_key": "z"}) is None


def test_b01_no_successful_skill_falls_back_to_agent() -> None:
    events = [
        {
            "event_type": "ASSISTANT_TURN",
            "payload": {"tool_calls": [_call("c1", "execute_skill", "demo")]},
        },
        {
            "event_type": "TOOL_CALL",
            "payload": {"tool_call_id": "c1", "tool_name": "execute_skill", "status": "ERROR"},
        },
    ]
    assert derive_task_family(events, agent_id="agent-1") == "agent:agent-1"
    assert derive_task_family([], agent_id="agent-1") == "agent:agent-1"


def test_b01_injection_diagnostics_match_selection_reasons() -> None:
    now = datetime.now(UTC)

    def record(key: str, state: str, *, author=MemoryAuthor.AGENT) -> MemoryRecord:
        return MemoryRecord(
            memory_key=key,
            content=FactContent(value="批量客户逐项处理"),
            author=author,
            category=MemoryCategory.PROCEDURE,
            version=1,
            updated_at=now,
            activation_state=state,
            task_signature="skill:demo",
            trigger_text="批量客户",
        )

    selection = select_injection(
        [record("exp.active", "ACTIVE"), record("exp.trial", "TRIAL")],
        query="批量客户",
        now=now,
        inject_inferred=True,
        task_family="skill:other",
        min_score=0.99,
        allowed_experience_keys=frozenset({"exp.active"}),
    )
    reasons = {item.memory_key: item.reason for item in selection.skipped}
    assert reasons["exp.trial"] == "NOT_ACTIVE"
    assert selection.items == () or all(
        item.record.memory_key == "exp.active" for item in selection.items
    )

    frozen = select_injection(
        [record("exp.active", "ACTIVE")],
        query="批量客户",
        now=now,
        inject_inferred=True,
        task_family="skill:other",
        min_score=0.0,
        allowed_experience_keys=frozenset(),
    )
    assert frozen.skipped[0].reason == "NOT_IN_FROZEN_SET"
