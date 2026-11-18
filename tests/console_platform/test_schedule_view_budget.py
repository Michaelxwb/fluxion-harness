"""TASK-018：Console 只投影预算来源与冻结值，不自己发明数值。"""

from __future__ import annotations

from muad_console_platform.application.schedule_view import schedule_detail


def test_schedule_detail_keeps_platform_budget_source() -> None:
    projected = schedule_detail(
        {
            "schedule_id": "0b8f3e4a-2f1c-4f7e-9c4a-0c2e1c8a9a01",
            "delivery_intent": "NONE",
            "budget_source": "PLATFORM",
            "budget": {"max_steps": 20, "max_tool_calls": 30, "max_tokens": 0},
        }
    )

    assert projected["budget_source"] == "PLATFORM"
    assert projected["budget"]["max_tokens"] == 0


def test_schedule_detail_keeps_none_for_reminder() -> None:
    projected = schedule_detail(
        {
            "schedule_id": "0b8f3e4a-2f1c-4f7e-9c4a-0c2e1c8a9a01",
            "delivery_intent": "NONE",
            "budget_source": "NONE",
        }
    )

    assert projected["budget_source"] == "NONE"
