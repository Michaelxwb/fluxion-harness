import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from muad_agent_runtime.application.async_tools.continuation_service import ExecutionLeaseLost
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.executor import ExecutorEvent, ExecutorFactory
from muad_agent_runtime.application.ports import NullPlatformSettingsClient
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.application.run_service import (
    RunService,
    build_snapshot,
    reap_abandoned_runs,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Artifact,
    CanonicalEvent,
    Conversation,
    RunRecord,
)
from muad_contracts import (
    AttachmentRef,
    ChannelContext,
    MessageInput,
    RunRequest,
    RunStatus,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.conftest import FakeResolveClient, TenantContext


def _request(tenant: TenantContext, text: str = "hello") -> RunRequest:
    return RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=tenant.platform_user_id,
        channel=ChannelContext(type="WECOM", bot_id="bot-1"),
        message=MessageInput(id=f"msg-{uuid.uuid4()}", text=text),
    )


async def _insert_run(
    tenant: TenantContext,
    status: str,
    *,
    lease_until: datetime | None = None,
    lease_owner: str | None = "instance-a",
) -> uuid.UUID:
    async with get_session_factory()() as session:
        conversation = Conversation(
            tenant_id=tenant.tenant_id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            status="ACTIVE",
            last_seq=0,
        )
        session.add(conversation)
        await session.flush()
        run = RunRecord(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            status=status,
            input_text="pending input",
            # 生产 Run 行总有受理时间：续跑的受理回执要把状态投影回给调用方（TASK-010）。
            start_time=datetime.now(UTC),
            deadline_at=datetime.now(UTC) + timedelta(minutes=2),
            trace_id=uuid.uuid4().hex,
            cancel_requested=False,
            lease_until=lease_until or datetime.now(UTC) + timedelta(seconds=60),
            lease_owner=lease_owner,
        )
        session.add(run)
        await session.commit()
        return run.id


async def test_cooperative_cancel_stops_running_execution(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
) -> None:
    gate = asyncio.Event()
    class GatedExecutor:
        def __init__(self, request):
            self.request = request
        async def run(self):
            yield ExecutorEvent("message.delta", {"delta": "started"})
            await gate.wait()
            if not await self.request.is_cancel_requested():
                yield ExecutorEvent("message.delta", {"delta": "done"})
    async def gated_factory(request):
        return GatedExecutor(request)
    session_factory = get_session_factory()
    async with session_factory() as session:
        service = RunService(
            session,
            fake_resolve,
            "instance-a",
            NullPlatformSettingsClient(),
            executor_factory=gated_factory,
            supervisor=ExecutionSupervisor(),
        )
        started = await service.start(_request(tenant), tenant.tenant_id)
        iterator = started.events
        first = await anext(iterator)
        assert first.type == "run.created"

        # `run.created` 只证明存储行已提交；执行任务可能还没把 Run 迁到 RUNNING。
        # 取消必须发生在协作式执行真正开始之后，否则会走「未运行直接取消」路径。
        for _ in range(500):
            async with session_factory() as probe:
                run = await probe.get(RunRecord, started.run_id)
                if run is not None and run.status == RunStatus.RUNNING:
                    break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("Run 未在取消前进入 RUNNING")

        async with session_factory() as cancel_session:
            canceller = RunService(
                cancel_session,
                fake_resolve,
                "instance-b",
                NullPlatformSettingsClient(),
                supervisor=ExecutionSupervisor(),
            )
            cancelled = await canceller.cancel_run(started.run_id, tenant.tenant_id)
            assert cancelled.status == RunStatus.RUNNING
            assert cancelled.cancel_requested is True
        gate.set()

        remaining = [event async for event in iterator]
        assert remaining[-1].type == "run.completed"
        assert remaining[-1].data["status"] == "CANCELLED"

    async with session_factory() as session:
        run = await session.get(RunRecord, started.run_id)
        assert run is not None
        assert run.status == "CANCELLED"
        assert run.end_time is not None
        _, event_types = await _business_events(session, started.conversation_id)
        assert event_types == ["USER_MESSAGE", "CANCEL"]


async def test_cancel_run_returns_cancelling_for_running_run(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    run_id = await _insert_run(tenant, "RUNNING")
    async with get_session_factory()() as session:
        service = RunService(
            session,
            fake_resolve,
            "instance-a",
            NullPlatformSettingsClient(),
            supervisor=ExecutionSupervisor(),
        )
        run = await service.cancel_run(run_id, tenant.tenant_id)
        assert run.status == RunStatus.RUNNING
        assert run.cancel_requested is True


async def test_reap_abandoned_marks_expired_lease_failed(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    expired_lease = datetime.now(UTC) - timedelta(seconds=60)
    fresh_lease = datetime.now(UTC) + timedelta(seconds=60)
    expired_run_id = await _insert_run(tenant, "RUNNING", lease_until=expired_lease)
    fresh_run_id = await _insert_run(tenant, "RUNNING", lease_until=fresh_lease)

    async with get_session_factory()() as session:
        reaped = await RunService(
            session, fake_resolve, "reaper", NullPlatformSettingsClient(), supervisor=ExecutionSupervisor()
        ).reap_abandoned()
    assert reaped == 1

    async with get_session_factory()() as session:
        expired = await session.get(RunRecord, expired_run_id)
        assert expired is not None
        assert expired.status == "FAILED"
        assert expired.error_code == "RUN_ABANDONED"
        assert expired.end_time is not None
        fresh = await session.get(RunRecord, fresh_run_id)
        assert fresh is not None
        assert fresh.status == "RUNNING"


async def test_reap_abandoned_module_function_counts_rows(
    tenant: TenantContext,
) -> None:
    await _insert_run(tenant, "RUNNING", lease_until=datetime.now(UTC) - timedelta(seconds=10))
    reaped = await reap_abandoned_runs(get_session_factory())
    assert reaped == 1


async def test_renew_lease_extends_only_running_runs(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    stale_lease = datetime.now(UTC) + timedelta(seconds=1)
    running_id = await _insert_run(tenant, "RUNNING", lease_until=stale_lease)
    completed_id = await _insert_run(tenant, "COMPLETED")

    async with get_session_factory()() as session:
        service = RunService(
            session,
            fake_resolve,
            "instance-a",
            NullPlatformSettingsClient(),
            supervisor=ExecutionSupervisor(),
        )
        assert await service._renew_lease(await session.get(RunRecord, running_id)) is True
        assert await service._renew_lease(await session.get(RunRecord, completed_id)) is False

    async with get_session_factory()() as session:
        run = await session.get(RunRecord, running_id)
        assert run is not None
        assert run.heartbeat_at is not None
        assert run.lease_until is not None
        assert run.lease_until > stale_lease


async def test_client_disconnect_keeps_supervised_execution_running(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
) -> None:
    """HTTP tail disconnect leaves the process-owned execution alive to complete."""
    gate = asyncio.Event()
    class GatedExecutor:
        async def run(self):
            yield ExecutorEvent("message.delta", {"delta": "started"})
            await gate.wait()
            yield ExecutorEvent("message.delta", {"delta": "done"})
    async def gated_factory(request):
        return GatedExecutor()
    supervisor = ExecutionSupervisor()
    session_factory = get_session_factory()
    async with session_factory() as session:
        service = RunService(
            session,
            fake_resolve,
            "instance-a",
            NullPlatformSettingsClient(),
            executor_factory=gated_factory,
            supervisor=supervisor,
        )
        started = await service.start(_request(tenant), tenant.tenant_id)
        iterator = started.events
        first = await anext(iterator)
        assert first.type == "run.created"
        # 领取/消费在后台任务里启动：等监督器真正接管后再断言持久状态
        async with asyncio.timeout(5):
            while supervisor.execution_count == 0:
                await asyncio.sleep(0.01)

        await iterator.aclose()

    async with session_factory() as session:
        run = await session.get(RunRecord, started.run_id)
        assert run is not None
        assert run.status == RunStatus.RUNNING
        assert run.cancel_requested is False
    assert supervisor.execution_count == 1
    gate.set()
    async with asyncio.timeout(5):
        while supervisor.execution_count:
            await asyncio.sleep(.01)
    async with session_factory() as session:
        run = await session.get(RunRecord, started.run_id)
        assert run.status == RunStatus.COMPLETED and not run.cancel_requested
    await supervisor.close()


async def _business_events(
    session: AsyncSession,
    conversation_id: uuid.UUID,
) -> tuple[list[int], list[str]]:
    rows = (
        await session.scalars(
            select(CanonicalEvent)
            .where(
                CanonicalEvent.conversation_id == conversation_id,
                CanonicalEvent.stream_type.is_(None),
            )
            .order_by(CanonicalEvent.seq)
        )
    ).all()
    return [row.seq for row in rows], [row.event_type for row in rows]


class RecordingCredentialsClient:
    def __init__(self, *, api_key: str = "sk-api09", secret: str = "mcp-secret") -> None:
        self.api_key = api_key
        self.secret = secret
        self.payloads: list[dict[str, object]] = []

    async def resolve_credentials(
        self, *, tenant_id: str, payload: dict[str, object], trace_id: str = ""
    ) -> dict[str, object]:
        self.payloads.append(payload)
        mcp_ids = payload.get("mcp_server_ids") or []
        return {
            "model": {"id": str(payload.get("model_id")), "api_key": self.api_key},
            "mcp_servers": [{"mcp_server_id": str(mcp_id), "auth_secret": self.secret} for mcp_id in mcp_ids],
        }


async def test_resume_uses_api09_credentials_in_memory(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    """resume 按冻结主键走 API-09；密钥只进内存 ExecutorRequest，不落 Snapshot。"""
    from muad_agent_runtime.application.executor import ExecutorRequest, RunExecutor

    from agent_runtime.conftest import FakeExecutor

    resolved = fake_resolve.response
    _, run_id = await _seed_waiting_run_with_snapshot(tenant, resolved)
    seen: list[ExecutorRequest] = []

    async def factory(request: ExecutorRequest) -> RunExecutor:
        seen.append(request)
        return FakeExecutor(request)

    credentials = RecordingCredentialsClient()
    async with get_session_factory()() as session:
        service = RunService(
            session,
            fake_resolve,
            "instance-a",
            NullPlatformSettingsClient(),
            executor_factory=factory,
            credentials_client=credentials,
            supervisor=ExecutionSupervisor(),
        )
        started = await service.resume(
            run_id, "answer", tenant.tenant_id,
            platform_user_id=tenant.platform_user_id, idempotency_key="k-1",
        )
        [event async for event in started.events]

    assert seen[0].model.api_key == "sk-api09"
    assert seen[0].credentials.mcp_secrets == {str(resolved.mcp_servers[0].mcp_server_id): "mcp-secret"}
    payload = credentials.payloads[0]
    assert payload["execution_ref"] == {"type": "RUN", "id": str(run_id)}
    assert payload["model_id"] == str(resolved.model.id)

    async with get_session_factory()() as session:
        from muad_agent_runtime.infrastructure.models.runtime import RuntimeSnapshot

        snapshot = await session.scalar(select(RuntimeSnapshot).where(RuntimeSnapshot.run_id == run_id))
        assert snapshot is not None
        assert "api_key" not in snapshot.model_json


async def _seed_waiting_run_with_snapshot(
    tenant: TenantContext,
    resolved: Any,
) -> tuple[uuid.UUID, uuid.UUID]:
    from muad_agent_runtime.application.run_service import build_snapshot
    from muad_agent_runtime.infrastructure.models.runtime import RunInterrupt

    conv_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conv_id,
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                status="ACTIVE",
                last_seq=0,
            )
        )
        run = RunRecord(
            id=run_id,
            tenant_id=tenant.tenant_id,
            conversation_id=conv_id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            status="WAITING_INPUT",
            input_text="hi",
            # 生产 Run 行总有受理时间：续跑的受理回执要把状态投影回给调用方（TASK-010）。
            start_time=datetime.now(UTC),
            deadline_at=datetime.now(UTC) + timedelta(minutes=2),
            trace_id=uuid.uuid4().hex,
            cancel_requested=False,
        )
        session.add(run)
        snapshot = build_snapshot(run_id=run_id, tenant_id=tenant.tenant_id, resolved=resolved)
        session.add(snapshot)
        await session.flush()
        run.snapshot_id = snapshot.id
        session.add(
            RunInterrupt(
                tenant_id=tenant.tenant_id,
                run_id=run_id,
                conversation_id=conv_id,
                interrupt_type="CONFIRM",
                prompt_text="continue?",
                options_json=["yes", "no"],
                status="WAITING",
            )
        )
        await session.commit()
    return conv_id, run_id


async def test_inbound_channel_reaches_executor_as_delivery_route(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
) -> None:
    """入站渠道持久化到 Run，并作为后台 Task/Schedule 的 delivery_route 传给执行器。"""
    captured: list[Any] = []

    async def capturing_factory(request: Any) -> Any:
        captured.append(request)
        return await executor_factory(request)

    request = RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=tenant.platform_user_id,
        channel=ChannelContext(
            type="WECOM", bot_id="bot-1", external_user_id="wotv-9", external_conversation_id="conv-9"
        ),
        message=MessageInput(id=f"msg-{uuid.uuid4()}", text="hi"),
    )
    async with get_session_factory()() as session:
        service = RunService(
            session,
            fake_resolve,
            "instance-a",
            NullPlatformSettingsClient(),
            executor_factory=capturing_factory,
            supervisor=ExecutionSupervisor(),
        )
        started = await service.start(request, tenant.tenant_id)
        _ = [event async for event in started.events]

    route = captured[0].run_context.delivery_route
    assert route is not None
    assert (route.bot_id, route.external_user_id, route.external_conversation_id) == (
        "bot-1",
        "wotv-9",
        "conv-9",
    )
    async with get_session_factory()() as session:
        run = await session.get(RunRecord, started.run_id)
    assert run is not None and run.channel_json["external_user_id"] == "wotv-9"


async def test_reaped_run_stops_the_executor_liveness_check(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    """[RULE-snapshot-001] Run 离开 RUNNING/WAITING_INPUT ⇒ 执行器必须停。

    这是执行器唯一的存活回调：它为 True 时 runner 在每次模型调用/工具调用前中止。此前它只看
    `cancel_requested`，于是被 Reaper 置 FAILED 的 Run 仍会继续建任务、发消息、写产物 —— 与
    接管它的那条 Run 同时产生副作用（2026-10-06 review）。
    """
    run_id = await _insert_run(tenant, "RUNNING")
    async with get_session_factory()() as session:
        service = RunService(
            session,
            fake_resolve,
            "instance-a",
            NullPlatformSettingsClient(),
            supervisor=ExecutionSupervisor(),
        )
        row = await session.get(RunRecord, run_id)
        assert await service._cancel_state_in_db(row) is False, "RUNNING with live identity can run"
        assert row is not None
        row.status = "FAILED"  # Reaper 按租约回收
        await session.commit()
        assert await service._cancel_state_in_db(row) is True, "已被回收 ⇒ 停"

        row.status = "WAITING_INPUT"  # 暂停等输入仍算"在跑"：那一轮要跑完收尾
        await session.commit()
        assert await service._cancel_state_in_db(row) is True


async def test_cancel_that_loses_the_cas_writes_no_fake_terminal_state(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    """[RULE-snapshot-001] cancel 的 CAS 零行 ⇒ 一行事件都不写，按事实返回。

    复现：Run 停在 WAITING_INPUT，用户取消的同时另一条 resume 抢先把状态翻成 RUNNING。
    此前零行更新的结果被丢弃，照旧写 CANCEL + RUN_COMPLETED(CANCELLED) ⇒ 库里 Run 是 RUNNING、
    事件日志却宣称已取消（2026-10-06 review）。
    """
    run_id = await _insert_run(tenant, "WAITING_INPUT")
    async with get_session_factory()() as session:
        service = RunService(
            session,
            fake_resolve,
            "instance-a",
            NullPlatformSettingsClient(),
            supervisor=ExecutionSupervisor(),
        )
        stale = await session.get(RunRecord, run_id)
        assert stale is not None and stale.status == "WAITING_INPUT"

        # 并发 resume 抢先：它的 CAS 条件正是 WAITING_INPUT
        async with get_session_factory()() as winner:
            row = await winner.get(RunRecord, run_id)
            assert row is not None
            row.status = "RUNNING"
            await winner.commit()

        cancelled = await service._cancel(stale)

    assert cancelled.status == "RUNNING", "CAS 落败 ⇒ 不谎报已取消"
    async with get_session_factory()() as session:
        row = await session.get(RunRecord, run_id)
        assert row is not None and row.status == "RUNNING"
        events = (await session.scalars(select(CanonicalEvent).where(CanonicalEvent.run_id == run_id))).all()
    assert [e.event_type for e in events if e.event_type in ("CANCEL", "RUN_COMPLETED")] == []


async def test_finalize_with_lost_terminal_cas_does_not_crash(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    """[RULE-snapshot-001] 终态 CAS 落败的收尾路径不得碰已过期的 ORM 对象。

    此前 rollback 之后继续用 `row`（`_terminal_event` 要读 id/conversation_id/status），异步
    会话下抛 MissingGreenlet（2026-10-06 review）。让 Run 停在 WAITING_INPUT，CAS（只认
    RUNNING）必然零行，确定性地走这条分支。
    """
    run_id = await _insert_run(tenant, "WAITING_INPUT")
    async with get_session_factory()() as session:
        service = RunService(
            session,
            fake_resolve,
            "instance-a",
            NullPlatformSettingsClient(),
            supervisor=ExecutionSupervisor(),
        )
        run = await session.get(RunRecord, run_id)
        assert run is not None
        with pytest.raises(ExecutionLeaseLost):
            await service._finalize_run(run, uuid.uuid4(), "done", agent_key="agent")
    async with get_session_factory()() as session:
        rows = (await session.scalars(select(CanonicalEvent).where(CanonicalEvent.run_id == run_id))).all()
    assert [r.event_type for r in rows if r.event_type in ("RUN_COMPLETED", "RUN_FAILED")] == []


async def test_auto_resume_keeps_attachments_of_the_new_message(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
) -> None:
    """[审查 2026-10-06] `WAITING_INPUT` 期间用户发来的附件必须与新文本一起进去。

    自动 resume 此前只取 `message.text`：附件既不落 `artifact` 行、也不进事件、更没进执行器，
    等于**静默丢弃**（用户以为把文件发出去了）。
    """
    run_id = await _insert_run(tenant, "WAITING_INPUT")
    async with get_session_factory()() as session:
        # 续跑从**已落库的快照**重建 Agent/Model/Skill/MCP ⇒ 这一行必须真的存在
        snapshot = build_snapshot(run_id=run_id, tenant_id=tenant.tenant_id, resolved=fake_resolve.response)
        session.add(snapshot)
        await session.flush()
        run = await session.get(RunRecord, run_id)
        assert run is not None
        run.snapshot_id = snapshot.id
        conversation_id = run.conversation_id
        await session.commit()
    captured: list[Any] = []

    async def capturing_factory(request: Any) -> Any:
        captured.append(request)
        return await executor_factory(request)

    request = RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=tenant.platform_user_id,
        conversation_id=conversation_id,
        channel=ChannelContext(type="WECOM", bot_id="bot-1"),
        message=MessageInput(
            id=f"msg-{uuid.uuid4()}",
            type="attachment",
            text="补充一句",
            attachments=[
                AttachmentRef(
                    storage_key="inbound/resume/0",
                    kind="DOCUMENT",
                    media_type="text/plain",
                    size=12,
                    filename="补充.txt",
                    checksum="sha256:" + "c" * 64,
                    source_channel="WECOM",
                )
            ],
        ),
    )
    async with get_session_factory()() as session:
        service = RunService(
            session,
            fake_resolve,
            "instance-a",
            NullPlatformSettingsClient(),
            executor_factory=capturing_factory,
            supervisor=ExecutionSupervisor(),
        )
        started = await service.start(request, tenant.tenant_id)
        events = [event async for event in started.events]
    assert events[0].data["resumed"] is True

    # ① 附件行落在**这个 Run** 上（与文本、事件同一事务）
    async with get_session_factory()() as session:
        rows = (await session.scalars(select(Artifact).where(Artifact.run_id == run_id))).all()
        assert [row.storage_key for row in rows] == ["inbound/resume/0"]

        # ② 事件载荷带上附件（历史装配据此渲染文本引用，不必回查产物表）
        event = (
            await session.scalars(
                select(CanonicalEvent)
                .where(
                    CanonicalEvent.run_id == run_id,
                    CanonicalEvent.event_type == "USER_MESSAGE",
                )
                .order_by(CanonicalEvent.seq.desc())
                .limit(1)
            )
        ).one()
    assert event.payload_json["text"] == "补充一句"
    assert [item["artifact_id"] for item in event.payload_json["attachments"]] == [str(rows[0].id)]

    # ③ 执行器看到的当前消息里带着它
    assert captured, "自动 resume 必须真的起执行器"
    content = captured[-1].input_content
    assert content is not None and "补充.txt" in str(content)


# --- TASK-003: `/new` 后「当前会话」解析（用户最近发言口径） -------------------------


async def _seed_conversation(tenant: TenantContext) -> uuid.UUID:
    """真实落一个会话行（`/new` 的产品路径是 `create_conversation`，此处只要空会话）。"""
    async with get_session_factory()() as session:
        conversation = Conversation(
            tenant_id=tenant.tenant_id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            status="ACTIVE",
            last_seq=0,
        )
        session.add(conversation)
        await session.commit()
        return conversation.id


async def _append_event(
    tenant: TenantContext,
    conversation_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """独立事务追加事件（真实 `EventWriter`）：`_next_seq` 会 bump 该会话的 `update_time`。"""
    async with get_session_factory()() as session:
        await EventWriter(session).append(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation_id,
            run_id=uuid.uuid4(),
            event_type=event_type,
            payload=payload,
        )
        await session.commit()


def _resolution_service(session: AsyncSession, fake_resolve: FakeResolveClient) -> RunService:
    return RunService(
        session,
        fake_resolve,
        "instance-a",
        NullPlatformSettingsClient(),
        supervisor=ExecutionSupervisor(),
    )


async def test_resolve_conversation_keeps_new_conversation_when_background_event_bumps_old(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    """[TASK-003] 旧会话的后台 append 不得把 `/new` 出的新会话顶掉。

    后台收尾向旧会话写 `MEMORY_CONSOLIDATED` 时，`EventWriter._next_seq` 会 bump 旧会话的
    `conversation.update_time`；解析若按 `update_time` 取「最新会话」，下一条入站（WS 不带
    会话指针）就落回旧会话。解析口径必须是**用户最近发言**，而不是任意事件的更新时间。
    """
    conversation_a = await _seed_conversation(tenant)
    await _append_event(tenant, conversation_a, "USER_MESSAGE", {"text": "第一句"})
    async with get_session_factory()() as session:
        conversation_b = await _resolution_service(session, fake_resolve).create_conversation(
            tenant.agent_id, tenant.platform_user_id, tenant.tenant_id, idempotency_key="new-1"
        )
        b_id = conversation_b.id
    await _append_event(tenant, conversation_a, "MEMORY_CONSOLIDATED", {"summary": "后台收尾"})

    async with get_session_factory()() as session:
        old = await session.get(Conversation, conversation_a)
        fresh = await session.get(Conversation, b_id)
        assert old is not None and fresh is not None
        assert old.update_time > fresh.create_time, "前提：后台事件 bump 的 update_time 已越过新会话"
        resolved = await _resolution_service(session, fake_resolve)._resolve_conversation(
            _request(tenant, "下一条"), tenant.tenant_id
        )
    assert resolved.id == b_id


async def test_resolve_conversation_falls_back_to_create_time_without_user_messages(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    """[TASK-003] 没有用户发言的会话按 `create_time` 排序，系统事件不得改变解析结果。

    ① 只有旧会话 A、且从未有用户发言 ⇒ 解析回落到 `create_time`：取 A 本身，而不是再造一行；
    ② A 被系统事件 bump 了 `update_time`、新会话 B 同样没有用户发言但创建更晚 ⇒ 必须得到 B。
    """
    conversation_a = await _seed_conversation(tenant)
    async with get_session_factory()() as session:
        resolved = await _resolution_service(session, fake_resolve)._resolve_conversation(
            _request(tenant, "下一条"), tenant.tenant_id
        )
        assert resolved.id == conversation_a, "无用户发言时回落 create_time：既有会话不得被替换成新行"

    async with get_session_factory()() as session:
        conversation_b = await _resolution_service(session, fake_resolve).create_conversation(
            tenant.agent_id, tenant.platform_user_id, tenant.tenant_id, idempotency_key="new-2"
        )
        b_id = conversation_b.id
    await _append_event(tenant, conversation_a, "RUN_FAILED", {"error_code": "RUN_ABANDONED"})

    async with get_session_factory()() as session:
        resolved = await _resolution_service(session, fake_resolve)._resolve_conversation(
            _request(tenant, "下一条"), tenant.tenant_id
        )
    assert resolved.id == b_id


async def test_resolve_conversation_sticks_to_conversation_that_received_user_message(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    """[TASK-003] 新会话 B 收到用户消息后仍解析到 B；旧会话再被系统事件 bump 也不改变。"""
    conversation_a = await _seed_conversation(tenant)
    await _append_event(tenant, conversation_a, "USER_MESSAGE", {"text": "第一句"})
    async with get_session_factory()() as session:
        conversation_b = await _resolution_service(session, fake_resolve).create_conversation(
            tenant.agent_id, tenant.platform_user_id, tenant.tenant_id, idempotency_key="new-3"
        )
        b_id = conversation_b.id
    await _append_event(tenant, conversation_a, "MEMORY_CONSOLIDATED", {"summary": "后台收尾"})
    await _append_event(tenant, b_id, "USER_MESSAGE", {"text": "第二句"})

    async with get_session_factory()() as session:
        resolved = await _resolution_service(session, fake_resolve)._resolve_conversation(
            _request(tenant, "下一条"), tenant.tenant_id
        )
        assert resolved.id == b_id

    # 旧会话再被后台事件 bump：解析仍取「用户最近发言」的 B
    await _append_event(tenant, conversation_a, "MEMORY_CONSOLIDATED", {"summary": "二次收尾"})
    async with get_session_factory()() as session:
        resolved = await _resolution_service(session, fake_resolve)._resolve_conversation(
            _request(tenant, "再下一条"), tenant.tenant_id
        )
        assert resolved.id == b_id


async def test_cancel_probe_throttles_pg_and_honours_hint() -> None:
    """取消探测：PG 只按兜底间隔读；Redis 提示命中立即用 PG 确认（DB 仍是权威）。"""
    from muad_agent_runtime.application.run_service import _CancelProbe

    db_calls: list[bool] = []
    hint = {"set": False}
    verdict = {"cancelled": False}

    async def check_db() -> bool:
        db_calls.append(True)
        return verdict["cancelled"]

    async def hint_set() -> bool:
        return hint["set"]

    probe = _CancelProbe(hint_set=hint_set, check_db=check_db, fallback_sec=1000.0)
    assert await probe.check() is False  # 首次立即核对 PG
    assert len(db_calls) == 1
    assert await probe.check() is False  # 兜底间隔内不打 PG
    assert len(db_calls) == 1
    hint["set"] = True
    verdict["cancelled"] = True
    assert await probe.check() is True  # 提示命中：立即由 PG 确认
    assert len(db_calls) == 2


async def test_process_admission_gate_bounds_concurrency() -> None:
    """准入闸门按事件循环共享：同时进入的准入数不超过上限（其余在 asyncio 排队）。"""
    from muad_agent_runtime.application import run_service as rs

    rs._process_admission_gates.clear()
    gate = rs.process_admission_gate(limit=2)
    active = 0
    peak = 0

    async def work() -> None:
        nonlocal active, peak
        async with gate:
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(work() for _ in range(6)))
    assert peak == 2
    assert rs.process_admission_gate(limit=2) is gate  # 同循环复用同一实例
    rs._process_admission_gates.clear()
