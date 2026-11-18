"""TASK-017 · `schedule.draft.ready`：持久确认卡片进入共享投影与历史重建链。

真实边界：真实 `publish_schedule_draft_ready`（真实 `EventWriter` 在会话行锁内分配 seq）、
真实 `public_events` 投影、真实 `WebchatHistoryService` 历史分页、真实 PostgreSQL；不 mock 存储。

关键断言（design §3.2.8 / §3.5.10、TASK-017 checklist 第 5 条）：

1. 卡片是**一条持久 canonical 事件**：历史与 SSE 接缝都从同一行投影出同一份 DTO，
   公开契约能原样解析；刷新页面走历史查询仍能看到（不是只在当次 SSE 里临时的）。
2. 同一 (Run, Draft, hash) 的重放拿回原 seq，不新增卡片行。
3. 它不是 Run 的 `confirmation.required`：不产生 Run interrupt，确认的是 Draft 而不是 Run。
4. 坏载荷显式失败且不回显私密字段；未登记的私有类型默认不公开。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.public_events import (
    PUBLIC_STREAM_EVENT_TYPES,
    project_public_fact,
    project_public_stream_event,
)
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.application.schedule_draft_events import (
    publish_schedule_draft_ready,
)
from muad_agent_runtime.application.webchat_history import HistoryScope, WebchatHistoryService
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    RunInterrupt,
    RunRecord,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.enums import DraftStatus, RunStatus
from muad_contracts.webchat import WebchatScheduleDraftReady
from muad_contracts.webchat_events import (
    WebchatScheduleDraftReadyEvent,
    decode_public_event_json,
    parse_public_event,
)

from agent_runtime.conftest import TenantContext

pytestmark = pytest.mark.integration

#: 本套件的入口（WEBCHAT 稳定入口账号；历史核属维度之一，不建 Console 行）。
ENTRY = uuid.uuid4()
BASE_TIME = datetime(2026, 11, 15, 9, 0, 0, tzinfo=UTC)


async def _seed_conversation(tenant: TenantContext) -> uuid.UUID:
    conversation_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                title="Draft 卡片会话",
                status="ACTIVE",
                last_seq=0,
                channel_account_id=ENTRY,
                last_message_at=BASE_TIME,
                create_time=BASE_TIME,
                update_time=BASE_TIME,
                is_deleted=False,
            )
        )
        await session.commit()
    return conversation_id


async def _seed_run(tenant: TenantContext, conversation_id: uuid.UUID) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                status=RunStatus.COMPLETED,
                input_text="每天 9 点提醒我开周会",
                trace_id="draft-card-trace",
                start_time=BASE_TIME,
                end_time=BASE_TIME,
                create_time=BASE_TIME,
                update_time=BASE_TIME,
            )
        )
        await session.commit()
    return run_id


@pytest.fixture
async def conversation(tenant: TenantContext) -> AsyncIterator[uuid.UUID]:
    yield await _seed_conversation(tenant)


@pytest.fixture
async def run_id(tenant: TenantContext, conversation: uuid.UUID) -> uuid.UUID:
    return await _seed_run(tenant, conversation)


def _card(**overrides: object) -> WebchatScheduleDraftReady:
    payload: dict[str, object] = {
        "draft_id": uuid.uuid4(),
        "draft_hash": "sha256:" + "a" * 64,
        "kind": "CREATE",
        "mode": "REMINDER",
        "preview": {
            "mode": "REMINDER",
            "payload": {"kind": "REMINDER", "text": "开周会"},
            "session_target": "isolated",
            "what": "开周会",
            "when": "CRON 0 9 * * * (Asia/Shanghai)",
            "first_fire_at": "2026-11-16T01:00:00+00:00",
            "delivery": "NONE",
            "tools_allow": [],
            "budget_source": "NONE",
            "dependencies": [],
            "budget": {},
            "diff": [],
        },
        "expires_at": "2026-11-16T01:00:00+00:00",
    }
    payload.update(overrides)
    return WebchatScheduleDraftReady.model_validate(payload)


async def _publish(
    tenant: TenantContext,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    card: WebchatScheduleDraftReady,
) -> int:
    return await publish_schedule_draft_ready(
        get_session_factory,
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        run_id=run_id,
        card=card,
    )


async def _rows(tenant_id: str) -> list[CanonicalEvent]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            sa.select(CanonicalEvent)
            .where(CanonicalEvent.tenant_id == tenant_id)
            .order_by(CanonicalEvent.seq)
        )
        return list(rows.all())


async def _history(tenant: TenantContext, conversation_id: uuid.UUID):
    scope = HistoryScope(
        tenant_id=tenant.tenant_id,
        user_id=tenant.platform_user_id,
        agent_id=tenant.agent_id,
        entry_id=ENTRY,
    )
    async with get_session_factory()() as session:
        return await WebchatHistoryService(session).list_messages(
            scope, conversation_id, page=1, page_size=20, through_seq=None
        )


# --------------------------------------------------------------------------------------
# 1. 持久行 → 共享投影 → 公开契约
# --------------------------------------------------------------------------------------


async def test_draft_card_projects_from_the_persisted_row(
    tenant: TenantContext, conversation: uuid.UUID, run_id: uuid.UUID
) -> None:
    card = _card()
    seq = await _publish(tenant, conversation, run_id, card)

    rows = await _rows(tenant.tenant_id)
    assert len(rows) == 1
    assert rows[0].seq == seq
    assert rows[0].stream_type == "schedule.draft.ready"
    assert rows[0].event_type == "SCHEDULE_DRAFT_READY"

    history_fact = project_public_fact(rows[0], {})
    stream_fact = project_public_stream_event(rows[0], {})
    assert isinstance(history_fact, WebchatScheduleDraftReadyEvent)
    assert isinstance(stream_fact, WebchatScheduleDraftReadyEvent)
    assert history_fact.data == card
    assert history_fact.seq == seq
    assert history_fact.run_id == run_id
    assert history_fact.conversation_id == conversation

    # 公开契约原样可解析（客户端拿到的是同一份 DTO，而不是内部载荷）。
    wire = history_fact.model_dump(mode="json")
    assert parse_public_event(wire) == history_fact
    assert decode_public_event_json(json.dumps(wire)) == history_fact


async def test_republishing_the_same_draft_replays_the_same_row(
    tenant: TenantContext, conversation: uuid.UUID, run_id: uuid.UUID
) -> None:
    card = _card()
    first = await _publish(tenant, conversation, run_id, card)
    retry = await _publish(tenant, conversation, run_id, card)

    assert retry == first
    assert len(await _rows(tenant.tenant_id)) == 1


async def test_draft_card_replays_after_a_reconnect_by_seq(
    tenant: TenantContext, conversation: uuid.UUID, run_id: uuid.UUID
) -> None:
    """重连按 `after_seq` 重放持久行：卡片不是一次性的 SSE 帧。"""
    first = _card()
    second = _card(draft_id=uuid.uuid4())
    await _publish(tenant, conversation, run_id, first)
    await _publish(tenant, conversation, run_id, second)

    async with get_session_factory()() as session:
        replayed = await EventWriter(session).list_events(
            conversation, tenant_id=tenant.tenant_id, after_seq=1
        )
    assert [row.payload_json["draft_id"] for row in replayed] == [str(second.draft_id)]
    event = project_public_stream_event(replayed[0], {})
    assert isinstance(event, WebchatScheduleDraftReadyEvent)
    assert event.data == second


async def test_draft_card_survives_a_history_refresh(
    tenant: TenantContext, conversation: uuid.UUID, run_id: uuid.UUID
) -> None:
    card = _card()
    seq = await _publish(tenant, conversation, run_id, card)

    page = await _history(tenant, conversation)
    assert page.through_seq == seq
    assert page.total == 1
    (item,) = page.items
    assert item.seq == seq
    assert item.role == "ASSISTANT"
    assert item.text == ""
    assert item.draft == card, "刷新后的历史必须带着可确认的卡片事实"


# --------------------------------------------------------------------------------------
# 2. 不是 Run 的确认通道
# --------------------------------------------------------------------------------------


async def test_draft_card_is_not_the_run_confirmation_channel(
    tenant: TenantContext, conversation: uuid.UUID, run_id: uuid.UUID
) -> None:
    await _publish(tenant, conversation, run_id, _card())

    async with get_session_factory()() as session:
        interrupts = await session.scalar(
            sa.select(sa.func.count())
            .select_from(RunInterrupt)
            .where(RunInterrupt.tenant_id == tenant.tenant_id)
        )
    assert interrupts == 0, "Draft 卡片不得制造 Run interrupt"

    assert PUBLIC_STREAM_EVENT_TYPES["schedule.draft.ready"] == "schedule.draft.ready"
    assert PUBLIC_STREAM_EVENT_TYPES["interrupt.required"] == "confirmation.required"


# --------------------------------------------------------------------------------------
# 3. 坏载荷显式失败；私有类型默认不公开
# --------------------------------------------------------------------------------------


async def _append_raw(
    tenant: TenantContext, conversation: uuid.UUID, run_id: uuid.UUID, payload: dict
) -> None:
    async with get_session_factory()() as session:
        await EventWriter(session).append(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation,
            run_id=run_id,
            event_type="SCHEDULE_DRAFT_READY",
            stream_type="schedule.draft.ready",
            payload=payload,
        )
        await session.commit()


async def test_malformed_draft_card_fails_explicitly_without_echoing_private_fields(
    tenant: TenantContext, conversation: uuid.UUID, run_id: uuid.UUID
) -> None:
    payload = _card().model_dump(mode="json")
    payload["private_canary"] = "secret-canary-value"
    await _append_raw(tenant, conversation, run_id, payload)

    (row,) = await _rows(tenant.tenant_id)
    with pytest.raises(AppError) as projection_error:
        project_public_fact(row, {})
    assert projection_error.value.code == ErrorCode.COMMON_INTERNAL_ERROR
    assert "secret-canary-value" not in str(projection_error.value.data)

    # 历史读取同样显式失败：已知公开事件的坏载荷不能被静默当过滤（否则用户以为任务已建好）。
    with pytest.raises(AppError) as history_error:
        await _history(tenant, conversation)
    assert history_error.value.code == ErrorCode.COMMON_INTERNAL_ERROR


async def test_private_lookalike_stream_is_never_projected(
    tenant: TenantContext, conversation: uuid.UUID, run_id: uuid.UUID
) -> None:
    async with get_session_factory()() as session:
        await EventWriter(session).append(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation,
            run_id=run_id,
            event_type="SCHEDULE_DRAFT_READY",
            stream_type="schedule.draft.private",
            payload=_card().model_dump(mode="json"),
        )
        await session.commit()

    (row,) = await _rows(tenant.tenant_id)
    assert project_public_fact(row, {}) is None
    assert project_public_stream_event(row, {}) is None
    page = await _history(tenant, conversation)
    assert page.items == [], "未登记的私有类型不能进入历史"


# --------------------------------------------------------------------------------------
# 5. 卡片带着确认状态回到历史：跨 Owner Schema 只读聚合（`harness-data` 同条约定）
# --------------------------------------------------------------------------------------


async def _seed_draft_row(
    tenant: TenantContext,
    draft_id: uuid.UUID,
    *,
    status: str = "PENDING",
    owner: uuid.UUID | None = None,
) -> None:
    """在 Worker 的 `task.task_schedule_draft` 里落一行真实 Draft（runtime 只读它）。"""
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO task.task_schedule_draft"
                " (id, tenant_id, owner_user_id, agent_id, kind, draft_hash,"
                " payload_json, preview_json, status, expires_at, idempotency_key)"
                " VALUES (:id, :tenant_id, :owner_user_id, :agent_id, 'CREATE', :draft_hash,"
                " '{}'::jsonb, '{}'::jsonb, :status, :expires_at, :idempotency_key)"
            ),
            {
                "id": draft_id,
                "tenant_id": tenant.tenant_id,
                "owner_user_id": owner if owner is not None else tenant.platform_user_id,
                "agent_id": tenant.agent_id,
                "draft_hash": "sha256:" + "a" * 64,
                "status": status,
                "expires_at": BASE_TIME,
                "idempotency_key": f"seed-{draft_id}",
            },
        )
        await session.commit()


async def test_history_card_carries_the_draft_confirmation_status(
    tenant: TenantContext, conversation: uuid.UUID, run_id: uuid.UUID
) -> None:
    """刷新后卡片自带确认状态：同一条历史在确认前后给出不同结论，客户端不再单独查一次。

    一页两张卡片也覆盖了批量查询的形状（一次 `id IN (...)`，不是逐卡关联）。
    """
    first = _card()
    second = _card(draft_id=uuid.uuid4())
    await _publish(tenant, conversation, run_id, first)
    await _publish(tenant, conversation, run_id, second)
    await _seed_draft_row(tenant, first.draft_id, status="PENDING")
    await _seed_draft_row(tenant, second.draft_id, status="CONFIRMED")

    by_draft = {
        item.draft.draft_id: item for item in (await _history(tenant, conversation)).items
    }
    assert by_draft[first.draft_id].draft_status == DraftStatus.PENDING
    assert by_draft[second.draft_id].draft_status == DraftStatus.CONFIRMED

    async with get_session_factory()() as session:
        await session.execute(
            sa.text("UPDATE task.task_schedule_draft SET status = 'CONFIRMED' WHERE id = :id"),
            {"id": first.draft_id},
        )
        await session.commit()

    confirmed = {
        item.draft.draft_id: item for item in (await _history(tenant, conversation)).items
    }
    assert confirmed[first.draft_id].draft_status == DraftStatus.CONFIRMED
    assert confirmed[first.draft_id].draft == first, "状态是叠加事实，不改卡片本身的公开载荷"


async def test_history_card_status_is_unknown_without_an_owned_draft(
    tenant: TenantContext, conversation: uuid.UUID, run_id: uuid.UUID
) -> None:
    """读不到 / 不是自己的 Draft 一律不可知：卡片不得因缺省状态被当成 PENDING。"""
    card = _card()
    await _publish(tenant, conversation, run_id, card)

    (missing,) = (await _history(tenant, conversation)).items
    assert missing.draft_status is None

    await _seed_draft_row(tenant, card.draft_id, owner=uuid.uuid4())
    (foreign,) = (await _history(tenant, conversation)).items
    assert foreign.draft_status is None, "跨用户的 Draft 不泄露状态"
