"""TASK-025 · WeCom 会话路由（S-01、S-10、E-08、E-09）：Worker 侧的持久化与读面。

真实边界：真实 PostgreSQL、真实 FastAPI 路由与受信身份头。断言只看行：

- PUSH Draft 确认后，不可变 Revision 带着**创建时的那个会话**（DeliveryRoute 行 + route_id），
  最终结果与 Human 问题都按它推送（TASK-022 用同一列）；
- 聊天入口按 id 读 Draft 时只拿得到自己的（跨用户/跨 agent 一律 404），hash 由服务端提供；
- 待处理读面按 `channel/bot/会话` 收窄时，只返回**推到这个会话**的 OPEN 提问。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_worker.application.schedule_drafts import schedule_draft_hash
from muad_agent_worker.infrastructure.models.task import (
    DeliveryRoute,
    TaskHumanInteraction,
    TaskSchedule,
    TaskScheduleDraft,
    TaskScheduleRevision,
)
from muad_contracts import ScheduleDraftRequest

from agent_worker.conftest import TenantContext
from agent_worker.helpers import internal_service_headers, persist_task
from agent_worker.test_scheduled_v2_confirm import confirm
from agent_worker.test_scheduled_v2_lifecycle import AGENT, OWNER, seed_schedule

pytestmark = pytest.mark.integration

TZ = "Asia/Shanghai"
PUSH_ROUTE = {
    "channel": "WECOM",
    "bot_id": "bot-demo",
    "external_user_id": "wotv-001",
    "external_conversation_id": "chat-001",
}
OTHER_ROUTE = {
    "channel": "WECOM",
    "bot_id": "bot-demo",
    "external_user_id": "wotv-001",
    "external_conversation_id": "chat-002",
}


def push_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "mode": "REMINDER",
        "instruction": "每天 8 点提醒我开会",
        "recurrence": {"kind": "CRON", "cron": "0 8 * * *", "timezone": TZ},
        "execution_spec": {"mode": "REMINDER", "reminder_text": "开会"},
        "delivery_intent": "PUSH",
        "delivery_route": dict(PUSH_ROUTE),
    }
    body.update(overrides)
    return body


def payload_of(body: dict[str, Any]) -> dict[str, Any]:
    return ScheduleDraftRequest.model_validate(body).model_dump(mode="json")


async def seed_draft(
    tenant: TenantContext,
    *,
    body: dict[str, Any],
    owner: uuid.UUID = OWNER,
    agent: uuid.UUID = AGENT,
    expires_in: int = 600,
) -> TaskScheduleDraft:
    payload = payload_of(body)
    now = datetime.now(UTC)
    draft = TaskScheduleDraft(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        owner_user_id=owner,
        agent_id=agent,
        kind="CREATE",
        draft_hash=schedule_draft_hash(payload),
        payload_json=payload,
        preview_json={
            "mode": payload["mode"],
            "what": "开会",
            "when": f"CRON 0 8 * * * ({TZ})",
            "first_fire_at": (now + timedelta(hours=1)).isoformat(),
            "delivery": "PUSH",
            "dependencies": [],
            "budget": {},
        },
        status="PENDING",
        expires_at=now + timedelta(seconds=expires_in),
        idempotency_key=f"draft-{uuid.uuid4()}",
    )
    async with tenant.session_factory() as session:
        session.add(draft)
        await session.commit()
    return draft


async def _route(tenant: TenantContext, route_id: uuid.UUID) -> DeliveryRoute | None:
    async with tenant.session_factory() as session:
        return await session.get(DeliveryRoute, route_id)


# ------------------------------------------------------------ PUSH 目标落库


async def test_confirm_persists_the_creation_route_on_the_revision(
    client: AsyncClient, tenant: TenantContext
) -> None:
    draft = await seed_draft(tenant, body=push_body())

    response = await confirm(
        client,
        tenant,
        draft,
        actor=OWNER,
        surface="WECOM",
        ref="wecom-message-1",
    )

    assert response.status_code == 200, response.text
    revision = await _revision_by_schedule(
        tenant, uuid.UUID(response.json()["data"]["schedule_id"])
    )
    assert revision.delivery_intent == "PUSH"
    assert revision.delivery_route_id is not None
    route = await _route(tenant, revision.delivery_route_id)
    assert route is not None
    # 冻结的是**创建时**那一个会话，且 owner 是 Draft 的 owner（不是调用方伪造的接收人）。
    assert route.channel == PUSH_ROUTE["channel"]
    assert route.bot_id == PUSH_ROUTE["bot_id"]
    assert route.external_user_id == PUSH_ROUTE["external_user_id"]
    assert route.external_conversation_id == PUSH_ROUTE["external_conversation_id"]
    assert route.platform_user_id == OWNER
    assert route.route_hash.startswith("sha256:")


async def test_push_draft_without_a_route_is_refused_before_any_side_effect(
    client: AsyncClient, tenant: TenantContext
) -> None:
    body = push_body()
    body.pop("delivery_route")
    response = await client.post(
        "/internal/schedule-drafts",
        json=body,
        headers=internal_service_headers(
            tenant.tenant_id,
            **{
                "X-Actor-User-Id": str(OWNER),
                "X-Agent-Id": str(AGENT),
                "X-Entry-Channel": "WECOM",
                "Idempotency-Key": f"draft-{uuid.uuid4()}",
            },
        ),
    )

    assert response.status_code == 400
    assert response.json()["code"] == "SCHEDULE_DRAFT_INVALID"
    async with tenant.session_factory() as session:
        assert (
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(TaskScheduleDraft)
                .where(TaskScheduleDraft.tenant_id == tenant.tenant_id)
            )
            == 0
        )


async def test_draft_route_must_match_the_creating_entry(
    client: AsyncClient, tenant: TenantContext
) -> None:
    response = await client.post(
        "/internal/schedule-drafts",
        json=push_body(delivery_route={**PUSH_ROUTE, "channel": "WEBCHAT"}),
        headers=internal_service_headers(
            tenant.tenant_id,
            **{
                "X-Actor-User-Id": str(OWNER),
                "X-Agent-Id": str(AGENT),
                "X-Entry-Channel": "WECOM",
                "Idempotency-Key": f"draft-{uuid.uuid4()}",
            },
        ),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "SCHEDULE_DELIVERY_UNSUPPORTED"
    assert response.json()["data"]["reason"] == "DELIVERY_ROUTE_MISMATCH"


# --------------------------------------------------------------- Draft 读面


async def test_chat_entry_reads_the_server_side_hash_only_for_its_owner(
    client: AsyncClient, tenant: TenantContext
) -> None:
    draft = await seed_draft(tenant, body=push_body())

    own = await client.get(
        f"/internal/schedule-drafts/{draft.id}",
        headers=internal_service_headers(
            tenant.tenant_id,
            **{"X-Actor-User-Id": str(OWNER), "X-Agent-Id": str(AGENT)},
        ),
    )
    other_actor = await client.get(
        f"/internal/schedule-drafts/{draft.id}",
        headers=internal_service_headers(
            tenant.tenant_id,
            **{"X-Actor-User-Id": str(uuid.uuid4()), "X-Agent-Id": str(AGENT)},
        ),
    )
    other_agent = await client.get(
        f"/internal/schedule-drafts/{draft.id}",
        headers=internal_service_headers(
            tenant.tenant_id,
            **{"X-Actor-User-Id": str(OWNER), "X-Agent-Id": str(uuid.uuid4())},
        ),
    )

    assert own.status_code == 200, own.text
    data = own.json()["data"]
    assert data["draft_id"] == str(draft.id)
    assert data["draft_hash"] == draft.draft_hash
    assert data["status"] == "PENDING"
    assert data["expires_at"]
    assert data["preview"]["delivery"] == "PUSH"
    # 越权读与"不存在"同形：不泄露 Draft 的存在性。
    assert other_actor.status_code == 404
    assert other_agent.status_code == 404


# ------------------------------------------------- 会话视角的待处理读面


async def test_pending_route_filter_returns_only_questions_pushed_to_that_chat(
    client: AsyncClient, tenant: TenantContext
) -> None:
    first = await _seed_open_question(tenant, route=PUSH_ROUTE, name="A 客户周报")
    second = await _seed_open_question(tenant, route=OTHER_ROUTE, name="B 客户日报")

    scoped = await client.get(
        "/internal/task-center/pending",
        params={
            "kind": "HUMAN",
            "agent_id": str(AGENT),
            "channel": PUSH_ROUTE["channel"],
            "bot_id": PUSH_ROUTE["bot_id"],
            "external_user_id": PUSH_ROUTE["external_user_id"],
            "external_conversation_id": PUSH_ROUTE["external_conversation_id"],
        },
        headers=internal_service_headers(
            tenant.tenant_id, **{"X-Actor-User-Id": str(OWNER)}
        ),
    )
    unscoped = await client.get(
        "/internal/task-center/pending",
        params={"kind": "HUMAN", "agent_id": str(AGENT)},
        headers=internal_service_headers(
            tenant.tenant_id, **{"X-Actor-User-Id": str(OWNER)}
        ),
    )
    half_scoped = await client.get(
        "/internal/task-center/pending",
        params={"kind": "HUMAN", "channel": PUSH_ROUTE["channel"]},
        headers=internal_service_headers(
            tenant.tenant_id, **{"X-Actor-User-Id": str(OWNER)}
        ),
    )

    assert scoped.status_code == 200, scoped.text
    assert [item["interaction_id"] for item in scoped.json()["data"]["items"]] == [first]
    assert unscoped.status_code == 200
    assert {item["interaction_id"] for item in unscoped.json()["data"]["items"]} == {
        first,
        second,
    }
    # 半个来源不是"全部"：显式拒绝，避免把别的会话的问题当成这个会话的。
    assert half_scoped.status_code == 422
    assert half_scoped.json()["code"] == "COMMON_VALIDATION_ERROR"


async def _seed_open_question(
    tenant: TenantContext, *, route: dict[str, str], name: str
) -> str:
    """一条推给指定会话的 OPEN 提问：真实 Schedule/Revision/Route/Execution/Interaction 链。"""
    seeded = await seed_schedule(tenant)
    route_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with tenant.session_factory() as session, session.begin():
        session.add(
            DeliveryRoute(
                id=route_id,
                tenant_id=tenant.tenant_id,
                channel=route["channel"],
                bot_id=route["bot_id"],
                platform_user_id=OWNER,
                external_user_id=route["external_user_id"],
                external_conversation_id=route["external_conversation_id"],
                route_json=dict(route),
                route_hash="sha256:" + uuid.uuid4().hex + uuid.uuid4().hex,
                status="ACTIVE",
            )
        )
        # 两条 CHECK 事实必须同一条 UPDATE 落：PUSH 必须有 route_id。
        revision = await session.get(TaskScheduleRevision, seeded.revision_id)
        assert revision is not None
        revision.delivery_intent = "PUSH"
        revision.delivery_route_id = route_id
        schedule = await session.get(TaskSchedule, seeded.schedule_id)
        assert schedule is not None
        schedule.name = name
    execution = await persist_task(
        tenant,
        agent_id=AGENT,
        actor_user_id=OWNER,
        status="WAITING_HUMAN",
        schedule_id=seeded.schedule_id,
        schedule_revision_id=seeded.revision_id,
        trigger_type="SCHEDULE",
        deadline_at=now + timedelta(hours=1),
        lease_owner=None,
        lease_until=None,
    )
    interaction_id = uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        session.add(
            TaskHumanInteraction(
                id=interaction_id,
                tenant_id=tenant.tenant_id,
                execution_id=execution.id,
                owner_user_id=OWNER,
                agent_id=AGENT,
                interaction_type="INPUT",
                status="OPEN",
                question=f"{name} 需要确认",
                deadline_at=now + timedelta(minutes=30),
            )
        )
    return str(interaction_id)


async def _revision_by_schedule(
    tenant: TenantContext, schedule_id: uuid.UUID
) -> TaskScheduleRevision:
    async with tenant.session_factory() as session:
        revision = await session.scalar(
            sa.select(TaskScheduleRevision).where(
                TaskScheduleRevision.tenant_id == tenant.tenant_id,
                TaskScheduleRevision.schedule_id == schedule_id,
                TaskScheduleRevision.revision_no == 1,
            )
        )
    assert revision is not None
    return revision
