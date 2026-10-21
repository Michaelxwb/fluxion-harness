"""B-231/B-05：[integration] 终态释放、取消与排队期限竞态（design §3.2 / §3.3 / §3.7）。

真实边界（不 mock 存储或租约）：
- 真实 `RunService`/`RunSubmissionService`/`AdmissionPolicyService` 业务路径（受理、取消、
  终态收尾、Reaper 回收、排队过期、领取全部走真实代码，不替换存储或租约）；
- 隔离租户的真实 PostgreSQL：`runtime.run_record`/`runtime_snapshot`/`canonical_event`/
  `run_submission`/`conversation`/`run_admission_policy`/`run_admission_counter`（只增删本租户行）；
- 真实 Console 内部取设置端点（uvicorn + 真实 socket + `control.platform_setting` 版本行，
  Runtime 侧用真实 `ConsolePlatformSettingsClient` 经真实 TCP 取快照）；
- 真实 PG 行锁：并发场景用阻塞事务在会话/Run 行上制造真实锁等待，不用 sleep 假装竞态。

关键断言（B-231/B-05 契约）：
1. 终态与 `admission_released_at` CAS/计数释放**同事务**：计数行被占时终态与释放都不可见，
   放行后一起出现；收尾事务被取消（真实回滚）时两者一起消失，份额不提前释放；
2. 取消与完成竞态：终态恰好一个、份额恰好释放一次（计数 2→1，双扣会落到 0）、计数不为负；
3. 排队期限在首次受理冻结 `min(受理+配置, 原 Run 绝对 deadline)`；到期后不得再被领取
   （不创建执行器/不调用模型），过期与领取只有一个结果；
4. 已开始执行（领取后）不被旧排队期限误杀：过期扫描不碰非排队行，按原 Run 绝对期限正常收尾；
5. 重复终态/接管/回收幂等：不双扣不欠账；早于准入计数的历史 Run 释放时截断在 0 且降级可见。
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.executor import (
    ExecutorEvent,
    ExecutorFactory,
    ExecutorRequest,
    RunExecutor,
)
from muad_agent_runtime.application.ports import NullPlatformSettingsClient
from muad_agent_runtime.application.run_service import RunService, RunStart
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.platform_settings_client import (
    ConsolePlatformSettingsClient,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_console_platform.infrastructure.db import get_session_factory as console_session_factory
from muad_console_platform.infrastructure.models.control import PlatformSetting
from muad_contracts import ChannelContext, MessageInput, ResolveDefinitionResponse, RunRequest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agent_runtime.conftest import CLEANUP_ORDER, FakeResolveClient, TenantContext

pytestmark = pytest.mark.integration

#: 内部服务身份：与 Console `require_service_identity` 同源（env `INTERNAL_SERVICE_TOKEN`）。
INTERNAL_TOKEN = "test-internal-token"
LISTEN_TIMEOUT_SEC = 15.0
WAIT_TIMEOUT_SEC = 30.0
LOCK_WAIT_TIMEOUT_SEC = 15.0
INSTANCE_ID = "instance-b231"
SCOPE_USER = "USER"
SCOPE_TENANT = "TENANT"
#: `runtime.run_record.queue_stage` 的排队态取值（与表级 CHECK 同源）。
QUEUE_STAGE = "WAITING_RUNTIME"
RUN_QUEUE_TIMEOUT = "RUN_QUEUE_TIMEOUT"
TERMINAL_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")


@pytest.fixture(autouse=True)
def _internal_service_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", INTERNAL_TOKEN)


async def _purge_tenant(tenant_id: str) -> None:
    """只清本租户的行（共享 dev 库纪律）：runtime 业务表 + 准入投影/计数 + 设置版本行。"""
    async with get_session_factory()() as session:
        for table in (*CLEANUP_ORDER, "run_admission_policy", "run_admission_counter"):
            await session.execute(
                sa.text(f"DELETE FROM runtime.{table} WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
        await session.commit()
    async with console_session_factory()() as session:
        await session.execute(
            sa.text("DELETE FROM control.platform_setting WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await session.commit()


@pytest.fixture
async def admission_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    try:
        yield tenant
    finally:
        await _purge_tenant(tenant.tenant_id)


# --------------------------------------------------------------------------------------
# 真实 HTTP 设置源：Console 内部端点
# --------------------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _serve(target: FastAPI) -> AsyncIterator[str]:
    """真实 uvicorn 单进程监听 127.0.0.1 随机端口，产出真实 base_url。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    config = uvicorn.Config(target, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        deadline = time.monotonic() + LISTEN_TIMEOUT_SEC
        while not server.started and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert server.started, "设置源未在超时内监听"
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=LISTEN_TIMEOUT_SEC)


@contextlib.asynccontextmanager
async def _console_source() -> AsyncIterator[str]:
    """真实 Console app（含内部取设置端点），真实 socket。"""
    from muad_console_platform.infrastructure import db as console_db
    from muad_console_platform.main import app as console_app

    console_db.get_engine.cache_clear()
    console_db.get_session_factory.cache_clear()
    async with _serve(console_app) as url:
        yield url


def _document(
    revision: int, *, tenant_limit: int, user_limit: int, queue_wait_timeout_sec: int = 60
) -> dict[str, Any]:
    return {
        "revision": revision,
        "settings": {
            "capacity": {
                "max_open_runs_per_tenant": tenant_limit,
                "max_open_runs_per_user": user_limit,
                "queue_wait_timeout_sec": queue_wait_timeout_sec,
            }
        },
    }


async def _seed_setting(tenant_id: str, *, revision: int, settings: dict[str, Any]) -> None:
    async with console_session_factory()() as session:
        session.add(PlatformSetting(tenant_id=tenant_id, revision=revision, settings_json=settings))
        await session.commit()


async def _seed_counter(tenant_id: str, *, scope_type: str, scope_key: str, count: int) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO runtime.run_admission_counter"
                " (tenant_id, scope_type, scope_key, open_run_count)"
                " VALUES (:tenant_id, :scope_type, :scope_key, :count)"
                " ON CONFLICT (tenant_id, scope_type, scope_key)"
                " DO UPDATE SET open_run_count = EXCLUDED.open_run_count"
            ).bindparams(
                tenant_id=tenant_id, scope_type=scope_type, scope_key=scope_key, count=count
            )
        )
        await session.commit()


async def _read_counter(tenant_id: str, *, scope_type: str, scope_key: str) -> int | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            sa.text(
                "SELECT open_run_count FROM runtime.run_admission_counter"
                " WHERE tenant_id = :tenant_id AND scope_type = :scope_type"
                " AND scope_key = :scope_key"
            ).bindparams(tenant_id=tenant_id, scope_type=scope_type, scope_key=scope_key)
        )


_RUN_COLUMNS = (
    "status, cancel_requested, error_code, admission_released_at, queue_stage, queue_reason,"
    " queued_at, queue_deadline_at, deadline_at, lease_owner, end_time"
)


async def _read_run(tenant_id: str, run_id: uuid.UUID) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.text(
                    f"SELECT {_RUN_COLUMNS} FROM runtime.run_record"
                    " WHERE tenant_id = :tenant_id AND id = :id"
                ),
                {"tenant_id": tenant_id, "id": run_id},
            )
        ).mappings().one()
    return dict(row)


async def _wait_until_running(
    tenant_id: str, run_id: uuid.UUID, *, timeout: float = 15.0
) -> dict[str, Any]:
    """受理在后台任务里领取执行：等 Run 真正进入 RUNNING（queue_stage 清空）再断言。"""
    deadline = time.monotonic() + timeout
    while True:
        row = await _read_run(tenant_id, run_id)
        if row["status"] == "RUNNING":
            return row
        if time.monotonic() > deadline:
            raise AssertionError(f"run {run_id} not claimed within {timeout}s: {row['status']}")
        await asyncio.sleep(0.02)


async def _terminal_event_count(tenant_id: str, run_id: uuid.UUID) -> int:
    async with get_session_factory()() as session:
        return int(
            await session.scalar(
                sa.text(
                    "SELECT count(*) FROM runtime.canonical_event"
                    " WHERE tenant_id = :tenant_id AND run_id = :id"
                    " AND stream_type IN ('run.completed', 'run.failed')"
                ),
                {"tenant_id": tenant_id, "id": run_id},
            )
        )


async def _last_run_failed_code(tenant_id: str, run_id: uuid.UUID) -> str | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            sa.text(
                "SELECT payload_json ->> 'error_code' FROM runtime.canonical_event"
                " WHERE tenant_id = :tenant_id AND run_id = :id AND stream_type = 'run.failed'"
                " ORDER BY seq DESC LIMIT 1"
            ).bindparams(tenant_id=tenant_id, id=run_id)
        )


async def _read_submission_status(tenant_id: str, run_id: uuid.UUID) -> str | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            sa.text(
                "SELECT status FROM runtime.run_submission"
                " WHERE tenant_id = :tenant_id AND run_id = :id"
            ).bindparams(tenant_id=tenant_id, id=run_id)
        )


async def _conversation_of(tenant_id: str, run_id: uuid.UUID) -> uuid.UUID:
    async with get_session_factory()() as session:
        value = await session.scalar(
            sa.text(
                "SELECT conversation_id FROM runtime.run_record"
                " WHERE tenant_id = :tenant_id AND id = :id"
            ).bindparams(tenant_id=tenant_id, id=run_id)
        )
    assert value is not None
    return uuid.UUID(str(value))


async def _rearm_deadlines(
    tenant_id: str, run_id: uuid.UUID, *, queue_deadline_at: datetime, deadline_at: datetime
) -> None:
    """重设两个期限（真实受理已把行、快照、提交、事件都建好；此处只调期限做竞态前置）。"""
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.run_record SET queue_deadline_at = :queue_deadline_at,"
                " deadline_at = :deadline_at WHERE tenant_id = :tenant_id AND id = :id"
            ).bindparams(
                tenant_id=tenant_id,
                id=run_id,
                queue_deadline_at=queue_deadline_at,
                deadline_at=deadline_at,
            )
        )
        await session.commit()


async def _expire_queue_deadlines() -> int:
    """真实排队过期扫描（生产由 Runtime 期限循环调用同一函数）。"""
    from muad_agent_runtime.application.run_service import expire_queued_runs

    return await expire_queued_runs(get_session_factory())


async def _wait_for_status(tenant_id: str, run_id: uuid.UUID, statuses: tuple[str, ...]) -> None:
    deadline = time.monotonic() + WAIT_TIMEOUT_SEC
    observed: dict[str, Any] = {}
    while time.monotonic() < deadline:
        observed = await _read_run(tenant_id, run_id)
        if observed["status"] in statuses:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"超时未到达 {statuses}：最后观测={observed}")


# --------------------------------------------------------------------------------------
# 真实 PG 行锁：阻塞事务与锁等待观测
# --------------------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _held_row_lock(engine: AsyncEngine, statement: str, **params: Any) -> AsyncIterator[None]:
    """在独立连接上持有行锁直到退出（退出即回滚释放），用于制造真实锁等待。"""
    connection = await engine.connect()
    transaction = await connection.begin()
    try:
        await connection.execute(sa.text(statement).bindparams(**params))
        yield
    finally:
        with contextlib.suppress(Exception):
            await transaction.rollback()
        await connection.close()


async def _wait_for_lock_waiters(engine: AsyncEngine, *, like: str, count: int = 1) -> None:
    """等到至少 `count` 个会话真实阻塞在行锁上（`pg_stat_activity` 的 Lock 等待）。"""
    deadline = time.monotonic() + LOCK_WAIT_TIMEOUT_SEC
    observed: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    sa.text(
                        "SELECT a.pid, a.state, a.wait_event_type, a.query"
                        " FROM pg_stat_activity a"
                        " WHERE a.state = 'active' AND a.wait_event_type = 'Lock'"
                        " AND a.query LIKE :like"
                    ).bindparams(like=like)
                )
            ).mappings().all()
        observed = [dict(row) for row in rows]
        if len(observed) >= count:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"超时未观察到 {count} 个会话在 {like} 上等待：最后观测={observed}")


# --------------------------------------------------------------------------------------
# Run 驱动：真实 RunService（替换的只有模型调用与定义解析）
# --------------------------------------------------------------------------------------


class _GatedExecutor:
    """模型调用替身：阻塞到 `release()`，让 Run 保持非终态（份额仍被占用）。"""

    def __init__(self, gate: asyncio.Event) -> None:
        self._gate = gate

    async def run(self) -> AsyncIterator[ExecutorEvent]:
        await self._gate.wait()
        yield ExecutorEvent(type="message.delta", data={"delta": "ok"})


class _GatedExecutorFactory:
    def __init__(self) -> None:
        self._gate = asyncio.Event()
        self.requests: list[ExecutorRequest] = []

    def release(self) -> None:
        self._gate.set()

    async def __call__(self, request: ExecutorRequest) -> RunExecutor:
        self.requests.append(request)
        return _GatedExecutor(self._gate)


@pytest.fixture
def gated_executor() -> _GatedExecutorFactory:
    return _GatedExecutorFactory()


def _request(
    tenant: TenantContext,
    *,
    text: str = "admission release",
    conversation_id: uuid.UUID | None = None,
    bot_id: str = "bot-wecom",
) -> RunRequest:
    return RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=tenant.platform_user_id,
        conversation_id=conversation_id,
        channel=ChannelContext(type="WECOM", bot_id=bot_id),
        message=MessageInput(id=f"msg-{uuid.uuid4()}", text=text),
    )


async def _start(
    tenant: TenantContext,
    settings_client: Any,
    fake_resolve: FakeResolveClient,
    executor_factory: ExecutorFactory,
    supervisor: ExecutionSupervisor,
    *,
    key: str,
    request: RunRequest | None = None,
) -> RunStart:
    payload = request if request is not None else _request(tenant)
    async with get_session_factory()() as session:
        service = RunService(
            session,
            fake_resolve,
            INSTANCE_ID,
            executor_factory=executor_factory,
            settings_client=settings_client,
            supervisor=supervisor,
        )
        return await service.start(payload, tenant.tenant_id, idempotency_key=key)


async def _cancel(tenant: TenantContext, run_id: uuid.UUID, fake_resolve: FakeResolveClient) -> None:
    """真实取消入口（`RunService.cancel_run`），不含执行装配。"""
    async with get_session_factory()() as session:
        service = RunService(
            session,
            fake_resolve,
            INSTANCE_ID,
            NullPlatformSettingsClient(),
            supervisor=ExecutionSupervisor(),
        )
        await service.cancel_run(run_id, tenant.tenant_id)


async def _drain(*starts: RunStart) -> None:
    for started in starts:
        async for _ in started.events:
            pass


def _short_deadline_resolve(
    resolved: ResolveDefinitionResponse, *, deadline_ms: int
) -> FakeResolveClient:
    agent = resolved.agent.model_copy(update={"runtime_config": {"deadline_ms": deadline_ms}})
    return FakeResolveClient(response=resolved.model_copy(update={"agent": agent}))


async def _seed_conversation(tenant: TenantContext) -> uuid.UUID:
    """真实会话行（受理入口按 conversation_id 核属后复用，不再新建）。"""
    conversation_id = uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO runtime.conversation (id, tenant_id, user_id, agent_id, status, last_seq)"
                " VALUES (:id, :tenant_id, :user_id, :agent_id, 'ACTIVE', 0)"
            ).bindparams(
                id=conversation_id, tenant_id=tenant.tenant_id, user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
            )
        )
        await session.commit()
    return conversation_id


async def _queued_run(
    tenant: TenantContext,
    client: ConsolePlatformSettingsClient,
    resolved: ResolveDefinitionResponse,
    gated_executor: _GatedExecutorFactory,
    supervisor: ExecutionSupervisor,
    *,
    key: str,
    conversation_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """真实受理产出「排队中」Run：绝对 deadline 1ms ⇒ 领取被期限拒绝，行停在 CREATED。

    该行带着真实快照/提交/事件与真实冻结排队投影，用于领取与过期竞态（不 mock 存储）。
    """
    started = await _start(
        tenant,
        client,
        _short_deadline_resolve(resolved, deadline_ms=1),
        gated_executor,
        supervisor,
        key=key,
        request=_request(tenant, conversation_id=conversation_id),
    )
    row = await _read_run(tenant.tenant_id, started.run_id)
    assert row["status"] == "CREATED", f"1ms 绝对期限的 Run 不得被领取：{row}"
    assert row["queue_stage"] == QUEUE_STAGE
    assert gated_executor.requests == [], "排队中的 Run 不得创建执行器"
    return started.run_id


async def _seed_open_run(tenant: TenantContext, *, status: str = "RUNNING") -> uuid.UUID:
    """种入一个在飞 Run 行（真实行；不含快照/提交，只用于占份额与接管场景）。"""
    conversation_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO runtime.conversation (id, tenant_id, user_id, agent_id, status, last_seq)"
                " VALUES (:id, :tenant_id, :user_id, :agent_id, 'ACTIVE', 0)"
            ).bindparams(
                id=conversation_id, tenant_id=tenant.tenant_id, user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
            )
        )
        await session.execute(
            sa.text(
                "INSERT INTO runtime.run_record (id, tenant_id, conversation_id, user_id, agent_id,"
                " status, input_text, trace_id, cancel_requested, lease_owner, lease_until)"
                " VALUES (:id, :tenant_id, :conversation_id, :user_id, :agent_id, :status, 'x',"
                " :trace_id, false, 'dead-instance', :lease_until)"
            ).bindparams(
                id=run_id, tenant_id=tenant.tenant_id, conversation_id=conversation_id,
                user_id=tenant.platform_user_id, agent_id=tenant.agent_id, status=status,
                trace_id=uuid.uuid4().hex, lease_until=datetime.now(UTC) - timedelta(minutes=5),
            )
        )
        await session.commit()
    return run_id


async def _seed_shares(tenant: TenantContext, *, count: int) -> None:
    """把**用户级**计数置为 `count`（真实计数行）：租户上限按在飞 Run 实时判定，无计数行。"""
    await _seed_counter(
        tenant.tenant_id, scope_type=SCOPE_USER, scope_key=str(tenant.platform_user_id), count=count
    )


async def _assert_shares(tenant: TenantContext, *, count: int) -> None:
    assert await _read_counter(
        tenant.tenant_id, scope_type=SCOPE_USER, scope_key=str(tenant.platform_user_id)
    ) == count


# --------------------------------------------------------------------------------------
# B-231 断言 1：终态与释放同事务
# --------------------------------------------------------------------------------------


async def test_b231_terminal_release_commits_in_one_transaction_with_terminal_state(
    admission_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-231] 计数行被占时终态与释放都不可见；放行后 COMPLETED 与计数 0 一起出现。"""
    tenant = admission_tenant
    engine: AsyncEngine = create_async_engine(SharedSettings().require_database_url())
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=5,
                settings=_document(5, tenant_limit=8, user_limit=4, queue_wait_timeout_sec=60)[
                    "settings"
                ],
            )
            started = await _start(
                tenant, client, FakeResolveClient(response=resolved), gated_executor,
                execution_supervisor_boundary, key="b231-tx-1",
            )
            await _assert_shares(tenant, count=1)
            created = await _wait_until_running(tenant.tenant_id, started.run_id)
            # 受理冻结排队投影；领取后清 stage、保留 queued_at/queue_deadline_at 作为历史
            assert created["status"] == "RUNNING" and created["queue_stage"] is None
            assert created["queue_deadline_at"] - created["queued_at"] == timedelta(seconds=60)
            assert created["queue_deadline_at"] < created["deadline_at"]

            async with _held_row_lock(
                engine,
                "SELECT open_run_count FROM runtime.run_admission_counter"
                " WHERE tenant_id = :tenant_id AND scope_type = 'USER' AND scope_key = :key"
                " FOR UPDATE",
                tenant_id=tenant.tenant_id,
                key=str(tenant.platform_user_id),
            ):
                gated_executor.release()
                await _wait_for_lock_waiters(engine, like="%run_admission_counter%")
                # 收尾事务未提交：终态与释放都还不可见，计数仍是 1（同事务）
                blocked = await _read_run(tenant.tenant_id, started.run_id)
                assert blocked["status"] == "RUNNING" and blocked["admission_released_at"] is None
                await _assert_shares(tenant, count=1)
            await _drain(started)
            final = await _read_run(tenant.tenant_id, started.run_id)
            assert final["status"] == "COMPLETED" and final["admission_released_at"] is not None
            assert final["queue_stage"] is None
            await _assert_shares(tenant, count=0)
            assert await _terminal_event_count(tenant.tenant_id, started.run_id) == 1
            assert await _read_submission_status(tenant.tenant_id, started.run_id) == "CLOSED"
        finally:
            await client.aclose()
    await engine.dispose()


async def test_b231_rolled_back_finalize_keeps_run_and_share(
    admission_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-231] 收尾事务被取消（真实回滚）：Run 仍 RUNNING、份额仍占用，不留下只释放不终态。"""
    tenant = admission_tenant
    engine: AsyncEngine = create_async_engine(SharedSettings().require_database_url())
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=6,
                settings=_document(6, tenant_limit=8, user_limit=4)["settings"],
            )
            started = await _start(
                tenant, client, FakeResolveClient(response=resolved), gated_executor,
                execution_supervisor_boundary, key="b231-tx-2",
            )
            await _assert_shares(tenant, count=1)

            async with _held_row_lock(
                engine,
                "SELECT open_run_count FROM runtime.run_admission_counter"
                " WHERE tenant_id = :tenant_id AND scope_type = 'USER' AND scope_key = :key"
                " FOR UPDATE",
                tenant_id=tenant.tenant_id,
                key=str(tenant.platform_user_id),
            ):
                gated_executor.release()
                await _wait_for_lock_waiters(engine, like="%run_admission_counter%")
                # 收尾卡在计数行锁上：取消承载执行的监督任务 ⇒ 收尾事务回滚（`close` 等它结束）
                await execution_supervisor_boundary.close()
            after = await _read_run(tenant.tenant_id, started.run_id)
            assert after["status"] == "RUNNING" and after["admission_released_at"] is None
            await _assert_shares(tenant, count=1)
            assert await _terminal_event_count(tenant.tenant_id, started.run_id) == 0
        finally:
            await client.aclose()
    await engine.dispose()


# --------------------------------------------------------------------------------------
# B-231 断言 2：取消与完成竞态
# --------------------------------------------------------------------------------------


async def test_b231_cancel_vs_completion_race_releases_exactly_once(
    admission_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-231] 取消与完成并发：终态恰好一个，份额恰好释放一次（2→1，不是 2→0）。"""
    tenant = admission_tenant
    engine: AsyncEngine = create_async_engine(SharedSettings().require_database_url())
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=7,
                settings=_document(7, tenant_limit=8, user_limit=4)["settings"],
            )
            started = await _start(
                tenant, client, FakeResolveClient(response=resolved), gated_executor,
                execution_supervisor_boundary, key="b231-race-1",
            )
            # 第二个在飞 Run 把共享计数抬到 2：让「恰好一次」可判别——双扣会把 2 压到 0
            await _seed_open_run(tenant)
            await _seed_shares(tenant, count=2)

            async with _held_row_lock(
                engine,
                "SELECT id FROM runtime.conversation WHERE id = :id FOR UPDATE",
                id=await _conversation_of(tenant.tenant_id, started.run_id),
            ):
                cancel_task = asyncio.create_task(
                    _cancel(tenant, started.run_id, FakeResolveClient(response=resolved))
                )
                await _wait_for_lock_waiters(engine, like="%runtime.conversation%")
                gated_executor.release()
                await _wait_for_lock_waiters(engine, like="%runtime.conversation%", count=2)
            await asyncio.wait_for(cancel_task, timeout=WAIT_TIMEOUT_SEC)
            await _drain(started)
            final = await _read_run(tenant.tenant_id, started.run_id)
            assert final["status"] in ("COMPLETED", "CANCELLED"), f"终态必须恰好一个：{final}"
            assert final["admission_released_at"] is not None
            assert await _terminal_event_count(tenant.tenant_id, started.run_id) == 1
            await _assert_shares(tenant, count=1)
            if final["status"] == "CANCELLED":
                assert final["cancel_requested"] is True
        finally:
            await client.aclose()
    await engine.dispose()


# --------------------------------------------------------------------------------------
# B-231 断言 3：排队期限冻结与过期释放
# --------------------------------------------------------------------------------------


async def test_b231_queue_deadline_freezes_at_admission(
    admission_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-231] queue_deadline=min(受理+配置, 原 Run 绝对 deadline)；领取后 stage 清空、期限保留。"""
    tenant = admission_tenant
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=8,
                settings=_document(8, tenant_limit=8, user_limit=4, queue_wait_timeout_sec=60)[
                    "settings"
                ],
            )
            started = await _start(
                tenant, client, FakeResolveClient(response=resolved), gated_executor,
                execution_supervisor_boundary, key="b231-freeze-1",
            )
            row = await _wait_until_running(tenant.tenant_id, started.run_id)
            assert row["status"] == "RUNNING" and row["queue_stage"] is None
            assert row["queued_at"] is not None
            assert row["queue_deadline_at"] - row["queued_at"] == timedelta(seconds=60)
            assert row["queue_deadline_at"] < row["deadline_at"]
            gated_executor.release()
            await _drain(started)

            # 配置 3600s > Run 绝对期限 5s ⇒ min 取 Run 绝对期限
            await _seed_setting(
                tenant.tenant_id,
                revision=9,
                settings=_document(9, tenant_limit=8, user_limit=4, queue_wait_timeout_sec=3600)[
                    "settings"
                ],
            )
            short = await _start(
                tenant, client, _short_deadline_resolve(resolved, deadline_ms=5_000),
                _GatedExecutorFactory(), execution_supervisor_boundary, key="b231-freeze-2",
            )
            row = await _wait_until_running(tenant.tenant_id, short.run_id)
            assert row["status"] == "RUNNING"
            assert row["queue_deadline_at"] == row["deadline_at"], "min 必须取原 Run 绝对期限"
        finally:
            await client.aclose()


async def test_b231_queue_expiry_fails_run_and_releases_once(
    admission_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-231] 排队过期：RUN_QUEUE_TIMEOUT 与释放同事务；重复过期/绝对期限过期都不再释放。"""
    tenant = admission_tenant
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=10,
                settings=_document(10, tenant_limit=8, user_limit=4)["settings"],
            )
            run_id = await _queued_run(
                tenant, client, resolved, gated_executor, execution_supervisor_boundary,
                key="b231-expire-1",
            )
            before = await _read_run(tenant.tenant_id, run_id)
            assert before["queue_deadline_at"] <= before["deadline_at"]
            await _assert_shares(tenant, count=1)

            assert await _expire_queue_deadlines() >= 1, "本租户的过期排队 Run 必须被扫描到"
            expired = await _read_run(tenant.tenant_id, run_id)
            assert expired["status"] == "FAILED" and expired["error_code"] == RUN_QUEUE_TIMEOUT
            assert expired["queue_stage"] is None and expired["queue_reason"] == RUN_QUEUE_TIMEOUT
            assert expired["admission_released_at"] is not None
            assert expired["lease_owner"] is None and expired["end_time"] is not None
            await _assert_shares(tenant, count=0)
            assert await _terminal_event_count(tenant.tenant_id, run_id) == 1
            assert await _last_run_failed_code(tenant.tenant_id, run_id) == RUN_QUEUE_TIMEOUT
            assert await _read_submission_status(tenant.tenant_id, run_id) == "CLOSED"
            assert gated_executor.requests == [], "排队过期后不得调用模型"

            # 幂等：重复过期与绝对期限过期都不再释放，也不再追加终态事件
            released_at = expired["admission_released_at"]
            await _expire_queue_deadlines()
            from muad_agent_runtime.application.run_deadline import expire_runs

            await expire_runs(get_session_factory())
            after = await _read_run(tenant.tenant_id, run_id)
            assert after["admission_released_at"] == released_at
            await _assert_shares(tenant, count=0)
            assert await _terminal_event_count(tenant.tenant_id, run_id) == 1
        finally:
            await client.aclose()


# --------------------------------------------------------------------------------------
# B-05：领取与排队期限只有一个结果；已开跑不被旧期限误杀
# --------------------------------------------------------------------------------------


async def test_b05_claim_and_queue_expiry_have_exactly_one_outcome(
    admission_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-05] 过期排队 Run：领取拒绝、过期恰一次；未来期限的排队 Run 则领取成功且不被误杀。"""
    from muad_agent_runtime.application.async_tools.continuation_service import claim_continuation

    tenant = admission_tenant
    engine: AsyncEngine = create_async_engine(SharedSettings().require_database_url())
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=11,
                settings=_document(11, tenant_limit=8, user_limit=4)["settings"],
            )
            factory = get_session_factory()
            expired_run = await _queued_run(
                tenant, client, resolved, gated_executor, execution_supervisor_boundary,
                key="b231-claim-1",
            )
            live_run = await _queued_run(
                tenant, client, resolved, gated_executor, execution_supervisor_boundary,
                key="b231-claim-2", conversation_id=await _seed_conversation(tenant),
            )
            now = datetime.now(UTC)
            # 两个 Run 的绝对期限都挪到未来；过期 Run 的排队期限留在过去，另一个留在未来
            await _rearm_deadlines(
                tenant.tenant_id, expired_run,
                queue_deadline_at=now - timedelta(seconds=60), deadline_at=now + timedelta(seconds=120),
            )
            await _rearm_deadlines(
                tenant.tenant_id, live_run,
                queue_deadline_at=now + timedelta(seconds=60), deadline_at=now + timedelta(seconds=120),
            )
            async with _held_row_lock(
                engine,
                "SELECT id FROM runtime.run_record WHERE tenant_id = :tenant_id AND id = :id FOR UPDATE",
                tenant_id=tenant.tenant_id, id=expired_run,
            ):
                claim = asyncio.create_task(
                    claim_continuation(
                        factory, instance_id=INSTANCE_ID, lease_sec=30, run_id=expired_run
                    )
                )
                await _wait_for_lock_waiters(engine, like="%runtime.run_record%")
                expiry = asyncio.create_task(_expire_queue_deadlines())
            claimed, expired_count = await asyncio.gather(
                asyncio.wait_for(claim, timeout=WAIT_TIMEOUT_SEC),
                asyncio.wait_for(expiry, timeout=WAIT_TIMEOUT_SEC),
            )
            after = await _read_run(tenant.tenant_id, expired_run)
            # 过期与领取只有一个结果：过期胜出（领取拒绝、份额释放恰一次），绝不混合
            assert claimed is None, "排队期限已过的 Run 不得被领取"
            assert expired_count >= 1
            assert after["status"] == "FAILED" and after["error_code"] == RUN_QUEUE_TIMEOUT
            assert after["admission_released_at"] is not None
            assert after["lease_owner"] is None
            assert gated_executor.requests == [], "过期分支不得调用模型"
            # 未来排队期限的 Run 领取成功：份额保留、stage 清空、过期扫描不再碰它
            claimed_now = await claim_continuation(
                factory, instance_id=INSTANCE_ID, lease_sec=30, run_id=live_run
            )
            assert claimed_now is not None and claimed_now.status == "RUNNING"
            live = await _read_run(tenant.tenant_id, live_run)
            assert live["queue_stage"] is None and live["admission_released_at"] is None
            assert live["lease_owner"] == INSTANCE_ID
            await _expire_queue_deadlines()
            still_running = await _read_run(tenant.tenant_id, live_run)
            assert still_running["status"] == "RUNNING", "已领取的行不得被排队过期误杀"
            assert still_running["admission_released_at"] is None
            await _assert_shares(tenant, count=1)
        finally:
            await client.aclose()
    await engine.dispose()


async def test_b05_claimed_run_is_not_killed_by_old_queue_deadline(
    admission_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-05] 已开跑的 Run（queue_stage 清空）即使旧排队期限已过也不被过期扫描误杀。"""
    tenant = admission_tenant
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=12,
                settings=_document(12, tenant_limit=8, user_limit=4)["settings"],
            )
            started = await _start(
                tenant, client, FakeResolveClient(response=resolved), gated_executor,
                execution_supervisor_boundary, key="b231-kill-1",
            )
            await _wait_until_running(tenant.tenant_id, started.run_id)
            now = datetime.now(UTC)
            await _rearm_deadlines(
                tenant.tenant_id, started.run_id,
                queue_deadline_at=now - timedelta(seconds=120), deadline_at=now + timedelta(seconds=120),
            )
            await _expire_queue_deadlines()
            running = await _read_run(tenant.tenant_id, started.run_id)
            assert running["status"] == "RUNNING" and running["admission_released_at"] is None
            gated_executor.release()
            await _drain(started)
            final = await _read_run(tenant.tenant_id, started.run_id)
            assert final["status"] == "COMPLETED" and final["error_code"] is None
            assert final["admission_released_at"] is not None
            await _assert_shares(tenant, count=0)
        finally:
            await client.aclose()


# --------------------------------------------------------------------------------------
# B-05：取消与超时竞态、幂等与不为负
# --------------------------------------------------------------------------------------


async def test_b05_resumed_run_is_not_governed_by_initial_queue_deadline(
    admission_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-05] 续跑/人工恢复（epoch ≥ 1）不受初始排队期限约束：不被过期扫描误杀、可正常领取。"""
    from muad_agent_runtime.application.async_tools.continuation_service import claim_continuation

    tenant = admission_tenant
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=16,
                settings=_document(16, tenant_limit=8, user_limit=4)["settings"],
            )
            run_id = await _queued_run(
                tenant, client, resolved, gated_executor, execution_supervisor_boundary,
                key="b231-resume-1",
            )
            now = datetime.now(UTC)
            await _rearm_deadlines(
                tenant.tenant_id, run_id,
                queue_deadline_at=now - timedelta(seconds=60), deadline_at=now + timedelta(seconds=120),
            )
            # 最坏情况：人工恢复后的行仍带初始排队投影（epoch ≥ 1 表示已被领取过）
            async with get_session_factory()() as session:
                await session.execute(
                    sa.text(
                        "UPDATE runtime.run_record SET execution_epoch = 1"
                        " WHERE tenant_id = :tenant_id AND id = :id"
                    ).bindparams(tenant_id=tenant.tenant_id, id=run_id)
                )
                await session.commit()

            await _expire_queue_deadlines()
            resumed = await _read_run(tenant.tenant_id, run_id)
            assert resumed["status"] == "CREATED" and resumed["admission_released_at"] is None
            claimed = await claim_continuation(
                get_session_factory(), instance_id=INSTANCE_ID, lease_sec=30, run_id=run_id
            )
            assert claimed is not None and claimed.status == "RUNNING"
            await _assert_shares(tenant, count=1)
        finally:
            await client.aclose()


async def test_b05_queue_expiry_vs_cancel_race_has_one_terminal_outcome(
    admission_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-05] 排队过期与取消并发：终态恰好一个、份额恰好释放一次（不双扣不欠账）。"""
    tenant = admission_tenant
    engine: AsyncEngine = create_async_engine(SharedSettings().require_database_url())
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=13,
                settings=_document(13, tenant_limit=8, user_limit=4)["settings"],
            )
            run_id = await _queued_run(
                tenant, client, resolved, gated_executor, execution_supervisor_boundary,
                key="b231-cancel-race-1",
            )
            now = datetime.now(UTC)
            await _rearm_deadlines(
                tenant.tenant_id, run_id,
                queue_deadline_at=now - timedelta(seconds=60), deadline_at=now + timedelta(seconds=120),
            )
            async with _held_row_lock(
                engine,
                "SELECT id FROM runtime.run_record WHERE tenant_id = :tenant_id AND id = :id FOR UPDATE",
                tenant_id=tenant.tenant_id, id=run_id,
            ):
                cancel = asyncio.create_task(
                    _cancel(tenant, run_id, FakeResolveClient(response=resolved))
                )
                await _wait_for_lock_waiters(engine, like="%runtime.run_record%")
                expiry = asyncio.create_task(_expire_queue_deadlines())
            await asyncio.wait_for(cancel, timeout=WAIT_TIMEOUT_SEC)
            await asyncio.wait_for(expiry, timeout=WAIT_TIMEOUT_SEC)
            final = await _read_run(tenant.tenant_id, run_id)
            assert final["status"] in ("CANCELLED", "FAILED"), f"终态必须恰好一个：{final}"
            assert final["admission_released_at"] is not None
            assert await _terminal_event_count(tenant.tenant_id, run_id) == 1
            await _assert_shares(tenant, count=0)
            if final["status"] == "CANCELLED":
                assert final["cancel_requested"] is True
            else:
                assert final["error_code"] == RUN_QUEUE_TIMEOUT
            assert gated_executor.requests == [], "取消/过期后不得调用模型"
        finally:
            await client.aclose()
    await engine.dispose()


async def test_b05_share_is_shared_across_entries_and_released_on_terminal(
    admission_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-05] 每用户跨入口受同一上限：一个入口终态释放后，另一个入口立刻可用该份额。"""
    tenant = admission_tenant
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=15,
                settings=_document(15, tenant_limit=8, user_limit=2)["settings"],
            )
            resolve = FakeResolveClient(response=resolved)
            wecom, webchat = _GatedExecutorFactory(), _GatedExecutorFactory()
            wecom_run = await _start(
                tenant, client, resolve, wecom, execution_supervisor_boundary, key="b231-entry-1",
                request=_request(
                    tenant, conversation_id=await _seed_conversation(tenant), bot_id="bot-wecom"
                ),
            )
            await _start(
                tenant, client, resolve, webchat, execution_supervisor_boundary, key="b231-entry-2",
                request=_request(
                    tenant, conversation_id=await _seed_conversation(tenant), bot_id="bot-webchat"
                ),
            )
            await _assert_shares(tenant, count=2)
            # 第三个 Run（另一入口账号/会话）被同一用户上限拒绝，且被拒的受理不占份额
            with pytest.raises(AppError) as denied:
                await _start(
                    tenant, client, resolve, _GatedExecutorFactory(), execution_supervisor_boundary,
                    key="b231-entry-3",
                    request=_request(
                        tenant, conversation_id=await _seed_conversation(tenant), bot_id="bot-webchat"
                    ),
                )
            assert denied.value.code == ErrorCode.RUN_CAPACITY_EXCEEDED
            await _assert_shares(tenant, count=2)

            # WECOM 入口的 Run 终态释放 ⇒ 份额立即可被另一入口复用
            wecom.release()
            await _drain(wecom_run)
            released = await _read_run(tenant.tenant_id, wecom_run.run_id)
            assert released["status"] == "COMPLETED"
            assert released["admission_released_at"] is not None
            await _assert_shares(tenant, count=1)
            admitted = await _start(
                tenant, client, resolve, _GatedExecutorFactory(), execution_supervisor_boundary,
                key="b231-entry-4",
                request=_request(
                    tenant, conversation_id=await _seed_conversation(tenant), bot_id="bot-webchat"
                ),
            )
            await _assert_shares(tenant, count=2)
            fresh = await _wait_until_running(tenant.tenant_id, admitted.run_id)
            assert fresh["status"] == "RUNNING" and fresh["admission_released_at"] is None
        finally:
            await client.aclose()


async def test_b05_repeated_terminal_and_takeover_do_not_double_release(
    admission_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-05] 双终态/接管/回收幂等：重复收尾与重复回收都不再释放，计数不为负。"""
    tenant = admission_tenant
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=14,
                settings=_document(14, tenant_limit=8, user_limit=4)["settings"],
            )
            started = await _start(
                tenant, client, FakeResolveClient(response=resolved), gated_executor,
                execution_supervisor_boundary, key="b231-repeat-1",
            )
            gated_executor.release()
            await _drain(started)
            done = await _read_run(tenant.tenant_id, started.run_id)
            assert done["status"] == "COMPLETED" and done["admission_released_at"] is not None

            # 接管：已被准入计数（计数=1）但租约过期的 RUNNING Run 由 Reaper 回收 ⇒ 恰释放一次
            takeover = await _seed_open_run(tenant)
            await _seed_shares(tenant, count=1)
            from muad_agent_runtime.application.run_deadline import expire_runs
            from muad_agent_runtime.application.run_service import reap_abandoned_runs

            factory = get_session_factory()
            assert await reap_abandoned_runs(factory) >= 1
            reaped = await _read_run(tenant.tenant_id, takeover)
            assert reaped["status"] == "FAILED" and reaped["error_code"] == "RUN_ABANDONED"
            assert reaped["admission_released_at"] is not None
            await _assert_shares(tenant, count=0)

            # 幂等：重复回收/过期/绝对期限扫描都不再释放，也不再追加终态事件
            released_at = done["admission_released_at"]
            await reap_abandoned_runs(factory)
            await _expire_queue_deadlines()
            await expire_runs(factory)
            await _assert_shares(tenant, count=0)
            after_done = await _read_run(tenant.tenant_id, started.run_id)
            assert after_done["admission_released_at"] == released_at
            assert await _terminal_event_count(tenant.tenant_id, started.run_id) == 1
            assert await _terminal_event_count(tenant.tenant_id, takeover) == 1
        finally:
            await client.aclose()


async def test_b05_already_released_run_never_decrements_again(
    admission_tenant: TenantContext,
) -> None:
    """[B-05] 已释放过的 Run（`admission_released_at` 非空）再次收尾：CAS 拒绝，不二次递减。"""
    tenant = admission_tenant
    run_id = await _seed_open_run(tenant)
    await _seed_shares(tenant, count=1)
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.run_record SET admission_released_at = now()"
                " WHERE tenant_id = :tenant_id AND id = :id"
            ).bindparams(tenant_id=tenant.tenant_id, id=run_id)
        )
        await session.commit()
    before = await _read_run(tenant.tenant_id, run_id)
    assert before["admission_released_at"] is not None

    from muad_agent_runtime.application.run_service import reap_abandoned_runs

    assert await reap_abandoned_runs(get_session_factory()) >= 1
    after = await _read_run(tenant.tenant_id, run_id)
    assert after["status"] == "FAILED" and after["error_code"] == "RUN_ABANDONED"
    assert after["admission_released_at"] == before["admission_released_at"], "CAS 不得重写释放时间"
    # 份额不再递减：CAS 落败的收尾路径只写终态，不碰共享计数
    await _assert_shares(tenant, count=1)


async def test_b05_release_without_counter_row_never_goes_negative(
    admission_tenant: TenantContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """[B-05] 早于准入计数的历史 Run 释放：计数行按 0 建立并截断在 0（不为负），降级可见。"""
    tenant = admission_tenant
    run_id = await _seed_open_run(tenant)
    assert await _read_counter(
        tenant.tenant_id, scope_type=SCOPE_USER, scope_key=str(tenant.platform_user_id)
    ) is None

    from muad_agent_runtime.application.run_service import reap_abandoned_runs

    with caplog.at_level("WARNING"):
        assert await reap_abandoned_runs(get_session_factory()) >= 1
    reaped = await _read_run(tenant.tenant_id, run_id)
    assert reaped["status"] == "FAILED" and reaped["admission_released_at"] is not None
    await _assert_shares(tenant, count=0)
    assert "admission_counter_floor" in caplog.text, "截断在 0 的降级必须可见（告警日志）"
