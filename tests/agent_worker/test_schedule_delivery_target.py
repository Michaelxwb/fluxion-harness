"""Schedule 推送目标独立于创建入口；目标列表只返回安全投影。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from muad_agent_worker.application.delivery_routes import list_active_delivery_routes
from muad_agent_worker.application.schedule_drafts import (
    ensure_delivery_target,
    resolve_delivery_intent,
)
from muad_agent_worker.infrastructure.models.task import DeliveryRoute
from muad_api import AppError
from muad_contracts import (
    DeliveryIntent,
    DeliveryRouteInput,
    DraftKind,
    Recurrence,
    RecurrenceKind,
    ReminderExecutionSpec,
    ScheduleDraftRequest,
)

from agent_worker.conftest import TenantContext
from agent_worker.helpers import internal_service_headers


def _body(
    *,
    intent: DeliveryIntent,
    route: DeliveryRouteInput | None,
) -> ScheduleDraftRequest:
    return ScheduleDraftRequest(
        kind=DraftKind.CREATE,
        mode="REMINDER",
        instruction="提醒",
        recurrence=Recurrence(
            kind=RecurrenceKind.ONCE,
            run_at=datetime.now(UTC) + timedelta(hours=1),
            timezone="Asia/Shanghai",
        ),
        execution_spec=ReminderExecutionSpec(reminder_text="提醒"),
        delivery_intent=intent,
        delivery_route=route,
    )


def test_web_entry_can_push_to_wecom() -> None:
    route = DeliveryRouteInput(channel="WECOM", bot_id="bot-1", external_user_id="user-1")
    body = _body(intent=DeliveryIntent.PUSH, route=route)

    assert resolve_delivery_intent(body.delivery_intent) is DeliveryIntent.PUSH
    ensure_delivery_target(body)  # 不再要求 route.channel == entry_channel


def test_web_entry_can_choose_none() -> None:
    body = _body(intent=DeliveryIntent.NONE, route=None)
    assert resolve_delivery_intent(body.delivery_intent) is DeliveryIntent.NONE
    ensure_delivery_target(body)


def test_push_route_must_be_an_outbound_channel() -> None:
    body = _body(
        intent=DeliveryIntent.PUSH,
        route=DeliveryRouteInput(channel="WEBCHAT", bot_id="bot-1", external_user_id="user-1"),
    )
    with pytest.raises(AppError) as caught:
        ensure_delivery_target(body)
    assert caught.value.code == "SCHEDULE_DELIVERY_UNSUPPORTED"


@pytest.mark.asyncio
async def test_list_active_delivery_routes_is_scoped_to_the_user(tenant: TenantContext) -> None:
    user_id = uuid.uuid4()
    async with tenant.session_factory() as session:
        session.add(
            DeliveryRoute(
                id=uuid.uuid4(),
                tenant_id=tenant.tenant_id,
                channel="WECOM",
                bot_id="bot-1",
                platform_user_id=user_id,
                external_user_id="user-1",
                external_conversation_id=None,
                route_json={"channel": "WECOM", "bot_id": "bot-1", "external_user_id": "user-1"},
                route_hash=f"hash-{uuid.uuid4()}",
                status="ACTIVE",
            )
        )
        await session.commit()

    async with tenant.session_factory() as session:
        rows = await list_active_delivery_routes(
            session, tenant_id=tenant.tenant_id, platform_user_id=user_id
        )
    assert [row.channel for row in rows] == ["WECOM"]


@pytest.mark.asyncio
async def test_delivery_targets_endpoint_returns_safe_items(
    tenant: TenantContext, client: AsyncClient
) -> None:
    user_id = uuid.uuid4()
    async with tenant.session_factory() as session:
        session.add(
            DeliveryRoute(
                id=uuid.uuid4(),
                tenant_id=tenant.tenant_id,
                channel="WECOM",
                bot_id="bot-1",
                platform_user_id=user_id,
                external_user_id="user-1",
                external_conversation_id=None,
                route_json={"channel": "WECOM", "bot_id": "bot-1", "external_user_id": "user-1"},
                route_hash=f"hash-{uuid.uuid4()}",
                status="ACTIVE",
            )
        )
        await session.commit()

    response = await client.get(
        "/internal/schedules/delivery-targets",
        headers=internal_service_headers(
            tenant.tenant_id, **{"X-Actor-User-Id": str(user_id)}
        ),
    )
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert items == [
        {
            "label": "WECOM",
            "channel": "WECOM",
            "route": {"channel": "WECOM", "bot_id": "bot-1", "external_user_id": "user-1"},
        }
    ]
