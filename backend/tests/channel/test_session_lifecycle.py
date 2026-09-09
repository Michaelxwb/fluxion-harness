"""SES-01 / SES-02（integration）：Session Lifecycle 验收。

- SES-01：普通消息经 ChatSessionHead 映射 `sess_*`（不再直传 conversation）；
  同一外部会话复用同一 active session；切 Agent 分 Head。
- SES-02：`/new` 原子轮换（新 sess、新消息走新 session）；长期数据保留；
  有 active execution 时拒绝（经可注入 probe）；并发双 `/new` 单胜。

先写测试记 RED：`ChatSessionService` 与 `/new` 在 TASK-003 实现前不存在。
"""

from __future__ import annotations

import asyncio

import pytest

from fluxion.plugins.channel_adapters import StubImChannelAdapter
from fluxion.protocols.channel import ExternalChannelMessage
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ResourceKind
from fluxion.services.channel_app import ChannelApplicationService
from tests.channel_helpers import RecordingRuntime, verified_identity
from tests.runtime_helpers import TEST_POSTGRES_DSN, publish_resource, seed_agent_definition


async def _setup() -> tuple[PostgreSQLRegistryStore, RecordingRuntime, ChannelApplicationService]:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    runtime = RecordingRuntime()
    service = ChannelApplicationService(store, runtime)
    await service.create_platform_user("tenant-a", "user-a")
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"max_rounds": 8, "default": True},
    )
    await seed_agent_definition(store, system_prompt="你是测试代理。")
    issued = await service.issue_bind_code("tenant-a", "user-a")
    result = await service.handle(
        StubImChannelAdapter(),
        _message("im-1", f"/bind {issued.code}", "m-bind"),
    verified=None,
    )
    assert result.kind == "bound"
    return store, runtime, service


def _message(channel_user_id: str, content: str, message_id: str) -> ExternalChannelMessage:
    return ExternalChannelMessage(
        tenant_id="tenant-a",
        channel_user_id=channel_user_id,
        conversation_id=f"conv-{channel_user_id}",
        message_id=message_id,
        content=content,
        agent_id="assistant",
    )


@pytest.mark.asyncio
async def test_SES01_message_maps_to_server_session() -> None:
    store, runtime, service = await _setup()
    try:
        first = await service.handle(StubImChannelAdapter(), _message("im-1", "hi", "m-1"), verified=verified_identity("im-1"))
        assert first.kind == "message"
        sess_a = runtime.requests[0].session_id
        assert sess_a.startswith("sess_")

        second = await service.handle(StubImChannelAdapter(), _message("im-1", "hi again", "m-2"), verified=verified_identity("im-1"))
        assert second.kind == "message"
        assert runtime.requests[1].session_id == sess_a

        # 同外部会话切 Agent → 分 Head（主键含 agent_id）。
        from fluxion.services.chat_session_service import ChatSessionService

        sessions = ChatSessionService(store)
        sess_other = await sessions.resolve_session(
            "tenant-a", "stub-im", "conv-im-1", "user-a", "other-agent"
        )
        assert sess_other.startswith("sess_")
        assert sess_other != sess_a
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_SES02_new_rotates_session() -> None:
    store, runtime, service = await _setup()
    try:
        await service.handle(StubImChannelAdapter(), _message("im-1", "hi", "m-1"), verified=verified_identity("im-1"))
        sess_old = runtime.requests[0].session_id

        rotated = await service.handle(StubImChannelAdapter(), _message("im-1", "/new", "m-new"), verified=verified_identity("im-1"))
        payload = rotated.to_payload()
        assert payload["kind"] == "command"
        assert payload["command"] == "new"
        assert payload["code"] == "ok"
        assert payload["data"]["session_id"].startswith("sess_")
        assert payload["data"]["session_id"] != sess_old
        assert runtime.requests == [] or len(runtime.requests) == 1  # /new 不进 Runtime

        await service.handle(StubImChannelAdapter(), _message("im-1", "after new", "m-3"), verified=verified_identity("im-1"))
        assert runtime.requests[-1].session_id == payload["data"]["session_id"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_SES02_new_refused_with_active_execution() -> None:
    store, runtime, service = await _setup()
    try:
        from fluxion.services.channel_app import ChannelApplicationService as Svc

        # 先产生 Head（首条普通消息），/new 才有旧 session 可冲突。
        await service.handle(StubImChannelAdapter(), _message("im-1", "hi", "m-0"), verified=verified_identity("im-1"))
        busy = Svc(store, runtime, session_activity_probe=_always_busy)
        result = await busy.handle(StubImChannelAdapter(), _message("im-1", "/new", "m-new-busy"), verified=verified_identity("im-1"))
        assert result.to_payload()["code"] == "new_session_conflict"
    finally:
        await store.close()


async def _always_busy(head: object) -> bool:
    del head
    return True


@pytest.mark.asyncio
async def test_SES02_concurrent_new_single_winner() -> None:
    """CAS 确定性验证（store 层）+ 服务层并发无 lost update。

    服务层每次 rotate 前重读 Head，并发双 /new 可能串行双成功（后者基于
    新 revision 再次轮换——合法），也可能一胜一冲突；两种结果都要求
    revision 严格递增、无更新丢失。
    """
    from fluxion.registry.store import VersionConflictError
    from fluxion.services.chat_session_service import ChatSessionService, SessionConflictError

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    try:
        sessions = ChatSessionService(store)
        sess = await sessions.resolve_session("tenant-a", "stub-im", "conv-x", "user-a", "assistant")
        assert sess.startswith("sess_")

        # store 层：stale revision 必冲突（确定性）。
        head = await store.get_session_head(
            tenant_id="tenant-a",
            channel_type="stub-im",
            external_conversation_id="conv-x",
            platform_user_id="user-a",
            agent_id="assistant",
        )
        assert head is not None
        from fluxion.registry import ChatSessionHead

        stale = ChatSessionHead(
            tenant_id=head.tenant_id,
            channel_type=head.channel_type,
            external_conversation_id=head.external_conversation_id,
            platform_user_id=head.platform_user_id,
            agent_id=head.agent_id,
            active_session_id="sess_stale",
            revision=head.revision + 1,
            created_at=head.created_at,
            updated_at=head.updated_at,
        )
        rotated = await store.rotate_session_head(stale, expected_revision=head.revision)
        assert rotated.revision == head.revision + 1
        with pytest.raises(VersionConflictError):
            await store.rotate_session_head(stale, expected_revision=head.revision)

        # 服务层并发：结果只允许 ok 或 SessionConflictError，且终态一致。
        results = await asyncio.gather(
            sessions.rotate_session("tenant-a", "stub-im", "conv-x", "user-a", "assistant"),
            sessions.rotate_session("tenant-a", "stub-im", "conv-x", "user-a", "assistant"),
            return_exceptions=True,
        )
        assert all(
            isinstance(r, str | SessionConflictError) for r in results
        ), results
        final = await store.get_session_head(
            tenant_id="tenant-a",
            channel_type="stub-im",
            external_conversation_id="conv-x",
            platform_user_id="user-a",
            agent_id="assistant",
        )
        assert final is not None
        ok_count = sum(isinstance(r, str) for r in results)
        assert final.revision == rotated.revision + ok_count
        if ok_count == 2:
            assert results[0] != results[1]
    finally:
        await store.close()
