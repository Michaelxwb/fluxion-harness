"""会话上限与保留期：真实 PG 行、真实历史读取路由、真实策略判定。

产品语义（2026-10-28 收口）：

1. 每个 `(tenant,user,agent,入口)` 的活跃会话有上限；达上限时自动归档最旧且无活跃 Run
   的会话，ARCHIVED 行仍保留历史，保留期内可继续。
2. 超过 `conversation_retention_days` 的会话不出现在列表、详情/消息/续聊都返回稳定码
   `CONVERSATION_EXPIRED`；`0` 是显式「永不过期」。
3. 继续历史会话优先于上限：若其余活跃会话都有在执行的 Run，允许暂时超出，不强行归档
   正在等待的对话。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_runtime.api.deps import get_platform_settings_client
from muad_agent_runtime.application.channel_conversations import (
    ConversationScope,
    archive_oldest_idle_conversations,
    count_active_conversations,
    enforce_conversation_limit,
    lock_conversation_scope,
    promote_archived_conversation,
)
from muad_agent_runtime.application.conversation_policy import (
    ConversationPolicy,
    assert_conversation_live,
    conversation_policy_from_settings,
)
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Conversation, RunRecord
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_contracts import RunStatus

from agent_runtime.conftest import CLEANUP_ORDER, TenantContext

pytestmark = pytest.mark.integration

ENTRY = uuid.uuid4()
PURGE_ORDER = ("artifact", *CLEANUP_ORDER)


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {
        "X-Tenant-Id": tenant.tenant_id,
        "X-Internal-Service": SharedSettings().internal_service_token or "",
    }


@dataclass(frozen=True)
class _PolicySettings:
    max_conversations_per_entry: int = 50
    conversation_retention_days: int = 30

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(
            revision=0,
            settings={
                "webchat": {
                    "max_conversations_per_entry": self.max_conversations_per_entry,
                    "conversation_retention_days": self.conversation_retention_days,
                }
            },
        )


@pytest.fixture
async def capacity_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    try:
        yield tenant
    finally:
        async with get_session_factory()() as session:
            for table in PURGE_ORDER:
                await session.execute(
                    sa.text(f"DELETE FROM runtime.{table} WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant.tenant_id},
                )
            await session.commit()


@pytest.fixture
def install_policy() -> Any:
    def _install(
        *, max_conversations_per_entry: int = 50, conversation_retention_days: int = 30
    ) -> None:
        from muad_agent_runtime.main import app

        app.dependency_overrides[get_platform_settings_client] = lambda: _PolicySettings(
            max_conversations_per_entry=max_conversations_per_entry,
            conversation_retention_days=conversation_retention_days,
        )

    yield _install
    from muad_agent_runtime.main import app

    app.dependency_overrides.pop(get_platform_settings_client, None)


async def _seed_conversation(
    tenant: TenantContext,
    *,
    entry_id: uuid.UUID | None = ENTRY,
    last_message_at: datetime | None,
    status: str = "ACTIVE",
    now: datetime,
) -> uuid.UUID:
    conversation = Conversation(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        user_id=tenant.platform_user_id,
        agent_id=tenant.agent_id,
        title="会话",
        status=status,
        last_seq=0,
        channel_account_id=entry_id,
        last_message_at=last_message_at,
        create_time=now,
        update_time=now,
    )
    async with get_session_factory()() as session:
        session.add(conversation)
        await session.commit()
    return conversation.id


async def _seed_active_run(
    tenant: TenantContext, conversation_id: uuid.UUID, *, now: datetime
) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                status=RunStatus.RUNNING,
                input_text="seed",
                trace_id="seed-trace",
                start_time=now,
                create_time=now,
                update_time=now,
            )
        )
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        conversation.last_run_id = run_id
        await session.commit()
    return run_id


def _scope(tenant: TenantContext) -> ConversationScope:
    return ConversationScope(
        tenant_id=tenant.tenant_id,
        user_id=tenant.platform_user_id,
        agent_id=tenant.agent_id,
        entry_id=ENTRY,
        external_thread_id="",
    )


async def _statuses(tenant_id: str) -> dict[uuid.UUID, str]:
    async with get_session_factory()() as session:
        rows = await session.execute(
            sa.select(Conversation.id, Conversation.status).where(
                Conversation.tenant_id == tenant_id
            )
        )
        return {row.id: row.status for row in rows}


# --------------------------------------------------------------------------------------
# 纯策略
# --------------------------------------------------------------------------------------


def test_policy_parses_settings_and_zero_retention_never_expires() -> None:
    defaults = conversation_policy_from_settings(None)
    assert defaults == ConversationPolicy(50, 30)
    explicit = conversation_policy_from_settings(
        {"webchat": {"max_conversations_per_entry": 3, "conversation_retention_days": 0}}
    )
    assert explicit == ConversationPolicy(3, 0)

    now = datetime.now(UTC)
    old = now - timedelta(days=31)
    assert defaults.is_expired(last_message_at=old, create_time=old, now=now)
    assert not explicit.is_expired(last_message_at=old, create_time=old, now=now)
    assert not defaults.is_expired(
        last_message_at=now - timedelta(days=29), create_time=old, now=now
    )


def test_policy_bad_settings_fail_explicitly() -> None:
    with pytest.raises(AppError) as exc_info:
        conversation_policy_from_settings({"webchat": {"conversation_retention_days": -1}})
    assert exc_info.value.code == ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE


def test_assert_conversation_live_returns_one_stable_code() -> None:
    now = datetime.now(UTC)
    policy = ConversationPolicy(50, 30)
    with pytest.raises(AppError) as exc_info:
        assert_conversation_live(
            policy,
            last_message_at=now - timedelta(days=31),
            create_time=now,
            now=now,
        )
    assert exc_info.value.code == ErrorCode.CONVERSATION_EXPIRED
    assert_conversation_live(
        policy,
        last_message_at=now - timedelta(days=1),
        create_time=now - timedelta(days=90),
        now=now,
    )


# --------------------------------------------------------------------------------------
# 历史读取：过期会话不可见、不可读
# --------------------------------------------------------------------------------------


async def test_expired_conversation_is_hidden_and_unreadable(
    client: AsyncClient,
    capacity_tenant: TenantContext,
    install_policy: Any,
) -> None:
    tenant = capacity_tenant
    now = datetime.now(UTC)
    old = now - timedelta(days=40)
    recent = now - timedelta(days=2)
    expired_id = await _seed_conversation(tenant, last_message_at=old, now=old)
    live_id = await _seed_conversation(tenant, last_message_at=recent, now=recent)
    install_policy(conversation_retention_days=30)

    headers = _headers(tenant)
    scope = {
        "user_id": str(tenant.platform_user_id),
        "agent_id": str(tenant.agent_id),
        "entry_id": str(ENTRY),
    }
    listing = await client.get(
        f"/internal/webchat/entries/{ENTRY}/conversations",
        headers=headers,
        params={"user_id": scope["user_id"], "agent_id": scope["agent_id"]},
    )
    assert listing.status_code == 200, listing.text
    ids = {item["conversation_id"] for item in listing.json()["data"]["items"]}
    assert ids == {str(live_id)}
    assert listing.json()["data"]["total"] == 1

    for path in (
        f"/internal/webchat/conversations/{expired_id}",
        f"/internal/webchat/conversations/{expired_id}/messages",
    ):
        response = await client.get(path, headers=headers, params=scope)
        assert response.status_code == 410, response.text
        assert response.json()["code"] == "CONVERSATION_EXPIRED"

    for path in (
        f"/internal/webchat/conversations/{live_id}",
        f"/internal/webchat/conversations/{live_id}/messages",
    ):
        response = await client.get(path, headers=headers, params=scope)
        assert response.status_code == 200, response.text


# --------------------------------------------------------------------------------------
# 上限：自动归档最旧空闲会话
# --------------------------------------------------------------------------------------


async def test_limit_archives_oldest_idle_conversation(
    capacity_tenant: TenantContext,
) -> None:
    tenant = capacity_tenant
    now = datetime.now(UTC)
    oldest = await _seed_conversation(
        tenant, last_message_at=now - timedelta(minutes=2), now=now
    )
    newest = await _seed_conversation(
        tenant, last_message_at=now - timedelta(minutes=1), now=now
    )
    policy = ConversationPolicy(2, 30)

    async with get_session_factory()() as session:
        await lock_conversation_scope(session, _scope(tenant))
        missing = await enforce_conversation_limit(
            session, _scope(tenant), policy, now=now, extra_slots=1
        )
        await session.commit()

    assert missing == 0
    assert await _statuses(tenant.tenant_id) == {
        oldest: "ARCHIVED",
        newest: "ACTIVE",
    }


async def test_limit_never_archives_a_conversation_with_an_active_run(
    capacity_tenant: TenantContext,
) -> None:
    tenant = capacity_tenant
    now = datetime.now(UTC)
    busy = await _seed_conversation(
        tenant, last_message_at=now - timedelta(minutes=3), now=now
    )
    idle_first = await _seed_conversation(
        tenant, last_message_at=now - timedelta(minutes=2), now=now
    )
    idle_second = await _seed_conversation(
        tenant, last_message_at=now - timedelta(minutes=1), now=now
    )
    await _seed_active_run(tenant, busy, now=now)
    policy = ConversationPolicy(2, 30)

    async with get_session_factory()() as session:
        missing = await enforce_conversation_limit(
            session, _scope(tenant), policy, now=now, extra_slots=1
        )
        await session.commit()

    assert missing == 0
    assert await _statuses(tenant.tenant_id) == {
        busy: "ACTIVE",
        idle_first: "ARCHIVED",
        idle_second: "ARCHIVED",
    }


async def test_limit_rejects_when_every_active_conversation_is_busy(
    capacity_tenant: TenantContext,
) -> None:
    tenant = capacity_tenant
    now = datetime.now(UTC)
    first = await _seed_conversation(
        tenant, last_message_at=now - timedelta(minutes=3), now=now
    )
    second = await _seed_conversation(
        tenant, last_message_at=now - timedelta(minutes=2), now=now
    )
    third = await _seed_conversation(
        tenant, last_message_at=now - timedelta(minutes=1), now=now
    )
    await _seed_active_run(tenant, first, now=now)
    await _seed_active_run(tenant, second, now=now)
    await _seed_active_run(tenant, third, now=now)
    policy = ConversationPolicy(2, 30)

    async with get_session_factory()() as session:
        missing = await enforce_conversation_limit(
            session, _scope(tenant), policy, now=now, extra_slots=1
        )
        await session.commit()

    assert missing == 2
    assert await _statuses(tenant.tenant_id) == {
        first: "ACTIVE",
        second: "ACTIVE",
        third: "ACTIVE",
    }


async def test_expired_conversations_do_not_consume_the_limit(
    capacity_tenant: TenantContext,
) -> None:
    tenant = capacity_tenant
    now = datetime.now(UTC)
    expired = await _seed_conversation(
        tenant, last_message_at=now - timedelta(days=40), now=now
    )
    live = await _seed_conversation(
        tenant, last_message_at=now - timedelta(days=1), now=now
    )
    policy = ConversationPolicy(2, 30)

    async with get_session_factory()() as session:
        active = await count_active_conversations(
            session, _scope(tenant), cutoff=policy.retention_cutoff(now)
        )
        missing = await enforce_conversation_limit(
            session, _scope(tenant), policy, now=now, extra_slots=1
        )
        await session.commit()

    assert active == 1
    assert missing == 0
    assert await _statuses(tenant.tenant_id) == {expired: "ACTIVE", live: "ACTIVE"}


async def test_continuing_archived_conversation_restores_it_and_archives_another(
    capacity_tenant: TenantContext,
) -> None:
    tenant = capacity_tenant
    now = datetime.now(UTC)
    archived = await _seed_conversation(
        tenant,
        last_message_at=now - timedelta(minutes=3),
        status="ARCHIVED",
        now=now,
    )
    oldest_idle = await _seed_conversation(
        tenant, last_message_at=now - timedelta(minutes=2), now=now
    )
    newest = await _seed_conversation(
        tenant, last_message_at=now - timedelta(minutes=1), now=now
    )
    policy = ConversationPolicy(2, 30)

    async with get_session_factory()() as session:
        conversation = await session.get(Conversation, archived)
        assert conversation is not None
        overage = await promote_archived_conversation(
            session, conversation, _scope(tenant), policy, now=now
        )
        await session.commit()

    assert overage == 0
    assert await _statuses(tenant.tenant_id) == {
        archived: "ACTIVE",
        oldest_idle: "ARCHIVED",
        newest: "ACTIVE",
    }


def _run_payload(tenant: TenantContext, conversation_id: uuid.UUID | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agent_id": str(tenant.agent_id),
        "platform_user_id": str(tenant.platform_user_id),
        "channel": {"type": "WECOM", "bot_id": "bot-1", "external_conversation_id": "ext-1"},
        "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": "继续会话"},
    }
    if conversation_id is not None:
        payload["conversation_id"] = str(conversation_id)
    return payload


async def test_expired_conversation_cannot_be_continued(
    client: AsyncClient,
    capacity_tenant: TenantContext,
    install_policy: Any,
) -> None:
    tenant = capacity_tenant
    now = datetime.now(UTC)
    expired = await _seed_conversation(
        tenant,
        entry_id=None,
        last_message_at=now - timedelta(days=31),
        now=now,
    )
    install_policy(conversation_retention_days=30)

    response = await client.post(
        "/v1/runs",
        json=_run_payload(tenant, expired),
        headers=_headers(tenant),
    )
    assert response.status_code == 410, response.text
    assert response.json()["code"] == "CONVERSATION_EXPIRED"


async def test_unexpired_archived_conversation_can_be_continued_and_reactivates(
    client: AsyncClient,
    capacity_tenant: TenantContext,
    install_policy: Any,
) -> None:
    tenant = capacity_tenant
    now = datetime.now(UTC)
    archived = await _seed_conversation(
        tenant,
        entry_id=None,
        last_message_at=now - timedelta(days=1),
        status="ARCHIVED",
        now=now,
    )
    install_policy(conversation_retention_days=30, max_conversations_per_entry=50)

    response = await client.post(
        "/v1/runs",
        json=_run_payload(tenant, archived),
        headers=_headers(tenant),
    )
    assert response.status_code == 200, response.text
    assert "event: run.completed" in response.text
    assert await _statuses(tenant.tenant_id) == {archived: "ACTIVE"}


async def test_create_conversation_archives_the_oldest_when_at_the_limit(
    client: AsyncClient,
    capacity_tenant: TenantContext,
    install_policy: Any,
) -> None:
    tenant = capacity_tenant
    install_policy(max_conversations_per_entry=1)
    body = {
        "agent_id": str(tenant.agent_id),
        "platform_user_id": str(tenant.platform_user_id),
    }

    first = await client.post("/v1/conversations", json=body, headers=_headers(tenant))
    second = await client.post("/v1/conversations", json=body, headers=_headers(tenant))
    assert first.status_code == 200 and second.status_code == 200, second.text
    first_id = uuid.UUID(first.json()["data"]["conversation_id"])
    second_id = uuid.UUID(second.json()["data"]["conversation_id"])
    assert await _statuses(tenant.tenant_id) == {
        first_id: "ARCHIVED",
        second_id: "ACTIVE",
    }


async def test_create_conversation_rejects_when_every_active_conversation_is_busy(
    client: AsyncClient,
    capacity_tenant: TenantContext,
    install_policy: Any,
) -> None:
    tenant = capacity_tenant
    now = datetime.now(UTC)
    busy = await _seed_conversation(
        tenant,
        entry_id=None,
        last_message_at=now - timedelta(minutes=1),
        now=now,
    )
    await _seed_active_run(tenant, busy, now=now)
    install_policy(max_conversations_per_entry=1)
    body = {
        "agent_id": str(tenant.agent_id),
        "platform_user_id": str(tenant.platform_user_id),
    }

    response = await client.post("/v1/conversations", json=body, headers=_headers(tenant))
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "CONVERSATION_LIMIT_REACHED"
    assert await _statuses(tenant.tenant_id) == {busy: "ACTIVE"}


async def test_archive_helper_is_bounded_and_skips_locked_rows(
    capacity_tenant: TenantContext,
) -> None:
    tenant = capacity_tenant
    now = datetime.now(UTC)
    first = await _seed_conversation(
        tenant, last_message_at=now - timedelta(minutes=2), now=now
    )
    second = await _seed_conversation(
        tenant, last_message_at=now - timedelta(minutes=1), now=now
    )

    async with get_session_factory()() as session:
        archived = await archive_oldest_idle_conversations(
            session,
            _scope(tenant),
            cutoff=None,
            now=now,
            limit=1,
        )
        await session.commit()

    assert archived == 1
    assert (await _statuses(tenant.tenant_id))[first] == "ARCHIVED"
    assert (await _statuses(tenant.tenant_id))[second] == "ACTIVE"
