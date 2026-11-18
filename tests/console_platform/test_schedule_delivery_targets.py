"""Console 只投影投递意图，不暴露 Worker 冻结的内部路由/外部会话 ID。"""

from __future__ import annotations

from muad_console_platform.application.schedule_view import schedule_detail, schedule_item


def test_schedule_detail_keeps_delivery_intent_and_drops_internal_route() -> None:
    detail = {
        "schedule_id": "0b8f3e4a-2f1c-4f7e-9c4a-0c2e1c8a9a01",
        "delivery_intent": "PUSH",
        "delivery_route_id": "6a0f1d2e-1111-4a2b-9c3d-4e5f60718293",
        "delivery_route": {"bot_id": "bot-internal", "external_user_id": "wx-user-1"},
        "next_fire_at": "2026-11-20T00:00:00+00:00",
    }

    projected = schedule_detail(detail)

    assert projected["delivery_intent"] == "PUSH"
    assert "delivery_route_id" not in projected
    assert "delivery_route" not in projected


def test_schedule_item_does_not_invent_a_write_target() -> None:
    """列表项没有目标字段：前端只能按 `delivery_intent` 展示“仅 Web/推送”，不能拼路由。"""
    item = {
        "schedule_id": "0b8f3e4a-2f1c-4f7e-9c4a-0c2e1c8a9a01",
        "delivery_intent": "NONE",
        "status": "ACTIVE",
    }

    assert schedule_item(item)["delivery_intent"] == "NONE"
    assert "delivery_route" not in schedule_item(item)
