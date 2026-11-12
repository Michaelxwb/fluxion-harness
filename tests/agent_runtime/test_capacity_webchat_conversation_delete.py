"""TASK-005：会话删除编排与通知收口（design backend §3.3/§3.4/§3.5）。

真实边界：真实 Runtime 应用与路由、真实 PostgreSQL（`conversation`/`run_record`/
`channel_notification` 都是真实行）、真实 `WebchatHistoryService` 读取与真实通知写入路径
`write_run_notification`。取消能力在服务层用例里用结构替身观察调用顺序与 `RUN_BUSY` 分支；
API 用例走真实 `RunService.cancel_run`（只打取消标记，不等 Run 终态），不触模型与外部服务。

关键断言：

1. 软删后列表/详情/消息一致不可见（产物下载的软删会话口径由
   `test_capacity_webchat_artifact_resolve.py` 的既有用例覆盖）；
2. 幂等：重复删除返回成功，且不再取消任何 Run；
3. 他人/不存在/无入口归属一律同形 `COMMON_NOT_FOUND`；
4. 活跃 Run 先取消再软删；取消遇 `RUN_BUSY`（恰好终态）视为已无活跃 Run；
5. 历史通知随会话软删；已删会话不再写新通知；未读数与其它会话不受影响。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_runtime.application.channel_notifications import write_run_notification
from muad_agent_runtime.application.conversation_deletion import ConversationDeletionService
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    ChannelNotification,
    Conversation,
    RunRecord,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_contracts.enums import RunStatus

from agent_runtime.conftest import TenantContext

pytestmark = pytest.mark.integration

#: 本套件的入口（WEBCHAT 稳定入口账号；真实核属维度之一，不建 Console 行）。
ENTRY = uuid.uuid4()
BASE_TIME = datetime(2026, 10, 18, 8, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------------------
# 真实 PG 播种与读取（只增删本租户的行；清理由 `tenant` fixture 统一完成）
# --------------------------------------------------------------------------------------


async def _seed_conversation(
    tenant: TenantContext,
    *,
    entry_id: uuid.UUID | None = ENTRY,
    user_id: uuid.UUID | None = None,
    is_deleted: bool = False,
) -> uuid.UUID:
    conversation_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant.tenant_id,
                user_id=user_id or tenant.platform_user_id,
                agent_id=tenant.agent_id,
                title="会话",
                status="ACTIVE",
                last_seq=0,
                channel_account_id=entry_id,
                create_time=BASE_TIME,
                update_time=BASE_TIME,
                is_deleted=is_deleted,
            )
        )
        await session.commit()
    return conversation_id


async def _seed_run(
    tenant: TenantContext,
    conversation_id: uuid.UUID,
    *,
    status: RunStatus = RunStatus.RUNNING,
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
                status=status,
                input_text="seed",
                trace_id="seed-trace",
                start_time=BASE_TIME,
                create_time=BASE_TIME,
                update_time=BASE_TIME,
            )
        )
        await session.commit()
    return run_id


async def _append_user_message(tenant: TenantContext, conversation_id: uuid.UUID) -> None:
    async with get_session_factory()() as session:
        await EventWriter(session).append(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation_id,
            run_id=uuid.uuid4(),
            event_type="USER_MESSAGE",
            payload={"text": "你好"},
        )
        await session.commit()


async def _write_completed_notification(
    tenant: TenantContext, run_id: uuid.UUID, *, delivery_key: str | None = None
) -> Any:
    async with get_session_factory()() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None
        receipt = await write_run_notification(
            session,
            run=run,
            kind="RUN_COMPLETED",
            delivery_key=delivery_key or f"run:{run_id}:terminal",
        )
        await session.commit()
    return receipt


async def _load_conversation(conversation_id: uuid.UUID) -> Conversation | None:
    async with get_session_factory()() as session:
        return await session.get(Conversation, conversation_id)


async def _load_run(run_id: uuid.UUID) -> RunRecord | None:
    async with get_session_factory()() as session:
        return await session.get(RunRecord, run_id)


async def _notifications(conversation_id: uuid.UUID) -> list[ChannelNotification]:
    async with get_session_factory()() as session:
        return list(
            await session.scalars(
                sa.select(ChannelNotification).where(
                    ChannelNotification.conversation_id == conversation_id
                )
            )
        )


async def _unread_count(tenant: TenantContext) -> int:
    async with get_session_factory()() as session:
        return int(
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(ChannelNotification)
                .where(
                    ChannelNotification.tenant_id == tenant.tenant_id,
                    ChannelNotification.user_id == tenant.platform_user_id,
                    ChannelNotification.read_at.is_(None),
                    ChannelNotification.is_deleted.is_(False),
                )
            )
            or 0
        )


class _FakeCancel:
    """取消端口的结构替身：记录调用，可选在执行前观察状态、或抛显式错误。"""

    def __init__(self, *, error: AppError | None = None, probe: Any = None) -> None:
        self.calls: list[tuple[uuid.UUID, str]] = []
        self._error = error
        self._probe = probe

    async def cancel_run(self, run_id: uuid.UUID, tenant_id: str) -> None:
        self.calls.append((run_id, tenant_id))
        if self._probe is not None:
            await self._probe(run_id)
        if self._error is not None:
            raise self._error


async def _delete(
    tenant: TenantContext,
    conversation_id: uuid.UUID,
    port: _FakeCancel,
    *,
    user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    async with get_session_factory()() as session:
        return await ConversationDeletionService(session, port).delete(
            tenant_id=tenant.tenant_id,
            user_id=user_id or tenant.platform_user_id,
            conversation_id=conversation_id,
        )


# --------------------------------------------------------------------------------------
# 服务层：软删、幂等、取消顺序、RUN_BUSY、核属
# --------------------------------------------------------------------------------------


async def test_delete_soft_deletes_conversation_and_its_notifications(
    tenant: TenantContext,
) -> None:
    """删除会话 = 会话行与它的通知一起软删；其它会话的通知与未读数不受影响。"""
    target = await _seed_conversation(tenant)
    target_run = await _seed_run(tenant, target, status=RunStatus.COMPLETED)
    await _write_completed_notification(tenant, target_run)
    other = await _seed_conversation(tenant)
    other_run = await _seed_run(tenant, other, status=RunStatus.COMPLETED)
    await _write_completed_notification(tenant, other_run)
    assert await _unread_count(tenant) == 2

    receipt = await _delete(tenant, target, _FakeCancel())

    assert receipt == {"conversation_id": str(target), "deleted": True}
    conversation = await _load_conversation(target)
    assert conversation is not None and conversation.is_deleted is True
    assert all(row.is_deleted for row in await _notifications(target))
    assert [row.is_deleted for row in await _notifications(other)] == [False]
    assert await _unread_count(tenant) == 1


async def test_delete_is_idempotent_and_stops_cancelling(tenant: TenantContext) -> None:
    """已删除的会话重复删除返回成功，且不再遍历活跃 Run（幂等分支先于取消）。"""
    conversation_id = await _seed_conversation(tenant)
    run_id = await _seed_run(tenant, conversation_id)
    port = _FakeCancel()

    first = await _delete(tenant, conversation_id, port)
    second = await _delete(tenant, conversation_id, port)

    assert first == second == {"conversation_id": str(conversation_id), "deleted": True}
    assert port.calls == [(run_id, tenant.tenant_id)]


async def test_delete_cancels_active_run_before_soft_deleting(tenant: TenantContext) -> None:
    """取消发生在软删之前：取消端口看到的会话仍是可见的。"""
    conversation_id = await _seed_conversation(tenant)
    run_id = await _seed_run(tenant, conversation_id)
    observed: list[bool] = []

    async def probe(_: uuid.UUID) -> None:
        conversation = await _load_conversation(conversation_id)
        assert conversation is not None
        observed.append(conversation.is_deleted)

    port = _FakeCancel(probe=probe)
    await _delete(tenant, conversation_id, port)

    assert observed == [False]
    assert port.calls == [(run_id, tenant.tenant_id)]


async def test_delete_tolerates_run_busy_when_cancel_races_terminal(
    tenant: TenantContext,
) -> None:
    """取消 CAS 落败（Run 恰好进入终态）不阻断删除。"""
    conversation_id = await _seed_conversation(tenant)
    await _seed_run(tenant, conversation_id)
    port = _FakeCancel(error=AppError(ErrorCode.RUN_BUSY))

    receipt = await _delete(tenant, conversation_id, port)

    assert receipt["deleted"] is True
    conversation = await _load_conversation(conversation_id)
    assert conversation is not None and conversation.is_deleted is True


@pytest.mark.parametrize("case", ["other_user", "missing", "no_entry"])
async def test_delete_is_not_found_for_unowned_or_missing(
    tenant: TenantContext, case: str
) -> None:
    """他人会话/不存在的 id/无入口归属会话一律同形 404，不泄露存在性。"""
    port = _FakeCancel()
    if case == "other_user":
        conversation_id = await _seed_conversation(tenant, user_id=uuid.uuid4())
    elif case == "no_entry":
        conversation_id = await _seed_conversation(tenant, entry_id=None)
    else:
        conversation_id = uuid.uuid4()

    with pytest.raises(AppError) as caught:
        await _delete(tenant, conversation_id, port)

    assert caught.value.code == ErrorCode.COMMON_NOT_FOUND
    assert port.calls == []
    if case != "missing":
        conversation = await _load_conversation(conversation_id)
        assert conversation is not None and conversation.is_deleted is False


# --------------------------------------------------------------------------------------
# 通知写入侧：已删会话不再产出新通知
# --------------------------------------------------------------------------------------


async def test_notification_write_is_skipped_for_deleted_conversation(
    tenant: TenantContext,
) -> None:
    """会话删除后，收尾 Run 的终态通知不再写入；删除前写入的通知随会话软删。"""
    conversation_id = await _seed_conversation(tenant)
    early_run = await _seed_run(tenant, conversation_id, status=RunStatus.COMPLETED)
    await _write_completed_notification(tenant, early_run)

    await _delete(tenant, conversation_id, _FakeCancel())

    late_run = await _seed_run(tenant, conversation_id, status=RunStatus.COMPLETED)
    receipt = await _write_completed_notification(tenant, late_run, delivery_key="run:late:terminal")

    assert receipt is None
    rows = await _notifications(conversation_id)
    assert len(rows) == 1
    assert rows[0].run_id == early_run and rows[0].is_deleted is True
    assert await _unread_count(tenant) == 0


# --------------------------------------------------------------------------------------
# API：真实路由 + 真实 RunService 取消语义
# --------------------------------------------------------------------------------------


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {
        "X-Tenant-Id": tenant.tenant_id,
        "X-Internal-Service": SharedSettings().internal_service_token or "",
    }


def _delete_path(conversation_id: uuid.UUID) -> str:
    return f"/internal/webchat/conversations/{conversation_id}"


async def _api_delete(client: AsyncClient, tenant: TenantContext, conversation_id: uuid.UUID) -> Any:
    return await client.delete(
        _delete_path(conversation_id),
        headers=_headers(tenant),
        params={"user_id": str(tenant.platform_user_id)},
    )


async def test_api_delete_hides_conversation_from_history_detail_and_messages(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """删除后列表/详情/消息一致不可见；重复删除仍返回成功（幂等）。"""
    conversation_id = await _seed_conversation(tenant)
    await _append_user_message(tenant, conversation_id)
    read_scope = {
        "user_id": str(tenant.platform_user_id),
        "agent_id": str(tenant.agent_id),
        "entry_id": str(ENTRY),
    }
    detail_path = f"/internal/webchat/conversations/{conversation_id}"
    before = await client.get(detail_path, headers=_headers(tenant), params=read_scope)
    assert before.status_code == 200, before.text

    first = await _api_delete(client, tenant, conversation_id)
    second = await _api_delete(client, tenant, conversation_id)

    assert first.status_code == 200, first.text
    assert first.json()["data"] == {"conversation_id": str(conversation_id), "deleted": True}
    assert second.status_code == 200, second.text
    detail = await client.get(detail_path, headers=_headers(tenant), params=read_scope)
    messages = await client.get(
        f"{detail_path}/messages", headers=_headers(tenant), params=read_scope
    )
    listed = await client.get(
        f"/internal/webchat/entries/{ENTRY}/conversations",
        headers=_headers(tenant),
        params={"user_id": str(tenant.platform_user_id), "agent_id": str(tenant.agent_id)},
    )
    assert detail.status_code == 404
    assert messages.status_code == 404
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"]["total"] == 0


async def test_api_delete_marks_active_run_cancelled_without_waiting(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """真实取消语义：请求内只打取消标记并返回，不等待 Run 进入终态。"""
    conversation_id = await _seed_conversation(tenant)
    run_id = await _seed_run(tenant, conversation_id, status=RunStatus.RUNNING)

    response = await _api_delete(client, tenant, conversation_id)

    assert response.status_code == 200, response.text
    run = await _load_run(run_id)
    assert run is not None
    assert run.cancel_requested is True
    assert run.status == RunStatus.RUNNING
    conversation = await _load_conversation(conversation_id)
    assert conversation is not None and conversation.is_deleted is True


async def test_api_delete_requires_internal_identity(
    client: AsyncClient, tenant: TenantContext, anonymous_client: AsyncClient
) -> None:
    """无内部服务身份一律拒绝（与既有受信面同一门控）。

    `client` 只为让本用例的依赖覆盖生效；请求必须走不带默认服务头的 `anonymous_client`。
    """
    conversation_id = await _seed_conversation(tenant)
    response = await anonymous_client.delete(
        _delete_path(conversation_id),
        headers={"X-Tenant-Id": tenant.tenant_id},
        params={"user_id": str(tenant.platform_user_id)},
    )
    assert response.status_code == 403
    conversation = await _load_conversation(conversation_id)
    assert conversation is not None and conversation.is_deleted is False
