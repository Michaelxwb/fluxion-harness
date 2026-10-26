"""B-230：[integration] Run 受理与准入计数同事务（design §3.2 / §3.4 / §3.7）。

真实边界（不 mock 存储或租约）：
- 真实 `RunService`/`RunSubmissionService`/`AdmissionPolicyService` 业务路径（设置快照冻结、
  Run/快照/提交/事件落库、幂等回放全部走真实代码）；
- 隔离租户的真实 PostgreSQL：`runtime.run_record`/`runtime_snapshot`/`canonical_event`/
  `run_submission`/`conversation`/`run_admission_policy`/`run_admission_counter`（只增删本租户的行）；
- 真实 Console 内部取设置端点（uvicorn + 真实 socket + `control.platform_setting` 版本行，
  Runtime 侧用真实 `ConsolePlatformSettingsClient` 经真实 TCP 取快照）；
- 脚本化设置探针（真实 HTTP，不是替身客户端）：给出指定版本序列，用于「一份快照只取一次」
  与「读到更低版本退出事务重取一次」两个判别性场景。

关键断言（B-230 契约）：
1. 建 Run 与 USER→TENANT 计数自增**同事务**提交：计数行锁被占时 Run 与计数都不可见，
   放行后一起出现；返回前 Run/快照/提交/事件已持久；
2. 全部非终态 Run 占用户/租户共享份额：RUNNING/WAITING_* 都占用，达阈值即拒；
3. 幂等重投不重复计数：同 key 同指纹回放原 Run、计数不变；并发同 key 恰好一个 Run；
4. 队列满不建假 Run：`RUN_CAPACITY_EXCEEDED` 整事务回滚（无 Run/会话/提交/事件，计数不变）；
5. 指纹含 tenant/endpoint/user/entry/conversation：换任一判别键即 `IDEMPOTENCY_MISMATCH`；
6. 低版本设置退出事务重取一次，绝不按旧阈值放行。
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
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
from muad_agent_runtime.infrastructure.models.runtime import RunRecord, RuntimeSnapshot
from muad_agent_runtime.infrastructure.platform_settings_client import (
    ConsolePlatformSettingsClient,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_console_platform.infrastructure.db import get_session_factory as console_session_factory
from muad_console_platform.infrastructure.models.control import PlatformSetting
from muad_contracts import ChannelContext, MessageInput, RunRequest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agent_runtime.conftest import CLEANUP_ORDER, FakeResolveClient, TenantContext

pytestmark = pytest.mark.integration

#: 内部服务身份：与 Console `require_service_identity` 同源（env `INTERNAL_SERVICE_TOKEN`）。
INTERNAL_TOKEN = "test-internal-token"
LISTEN_TIMEOUT_SEC = 15.0
WAIT_TIMEOUT_SEC = 30.0
INSTANCE_ID = "instance-b230"
SCOPE_USER = "USER"
SCOPE_TENANT = "TENANT"


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
# 真实 HTTP 设置源：Console 内部端点 / 脚本化探针
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


@dataclass(slots=True)
class _ScriptedSettingsSource:
    """真实 HTTP 设置源探针：按脚本给出（revision, settings）序列，并记录每次到达。

    `script` 的最后一个文档反复生效——用于「取配置 → 读到更低版本 → 退出事务重取一次」。
    """

    arrivals: list[str]
    app: FastAPI
    _scripts: dict[str, list[dict[str, Any]]]

    @classmethod
    def create(cls) -> _ScriptedSettingsSource:
        source = cls(arrivals=[], app=FastAPI(), _scripts={})
        source.app.get("/internal/v1/platform-settings")(source._handle)
        return source

    def script(self, tenant_id: str, *documents: dict[str, Any]) -> None:
        assert documents, "至少给一个版本文档"
        self._scripts[tenant_id] = list(documents)

    async def _handle(self, request: Request) -> JSONResponse:
        tenant_id = request.headers.get("x-tenant-id", "")
        self.arrivals.append(tenant_id)
        documents = self._scripts.get(tenant_id)
        if not documents:
            return JSONResponse({"code": "ok", "data": {"revision": 0, "settings": {}}})
        document = documents.pop(0) if len(documents) > 1 else documents[0]
        return JSONResponse(
            {"code": "ok", "data": {"revision": document["revision"], "settings": document["settings"]}}
        )


@contextlib.asynccontextmanager
async def _scripted_source() -> AsyncIterator[tuple[_ScriptedSettingsSource, str]]:
    source = _ScriptedSettingsSource.create()
    async with _serve(source.app) as url:
        yield source, url


def _document(
    revision: int, *, tenant_limit: int, user_limit: int, max_turns: int | None = None
) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "capacity": {
            "max_open_runs_per_tenant": tenant_limit,
            "max_open_runs_per_user": user_limit,
        }
    }
    if max_turns is not None:
        settings["agent"] = {"max_turns": max_turns}
    return {"revision": revision, "settings": settings}


# --------------------------------------------------------------------------------------
# 真实 PG 行：设置版本、策略/计数、会话与 Run
# --------------------------------------------------------------------------------------


async def _seed_setting(tenant_id: str, *, revision: int, settings: dict[str, Any]) -> None:
    async with console_session_factory()() as session:
        session.add(PlatformSetting(tenant_id=tenant_id, revision=revision, settings_json=settings))
        await session.commit()


async def _seed_policy(
    tenant_id: str, *, revision: int, tenant_limit: int, user_limit: int
) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO runtime.run_admission_policy"
                " (tenant_id, source_settings_revision, max_open_runs_per_tenant,"
                "  max_open_runs_per_user)"
                " VALUES (:tenant_id, :revision, :tenant_limit, :user_limit)"
            ).bindparams(
                tenant_id=tenant_id,
                revision=revision,
                tenant_limit=tenant_limit,
                user_limit=user_limit,
            )
        )
        await session.commit()


async def _seed_counter(tenant_id: str, *, scope_type: str, scope_key: str, count: int) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO runtime.run_admission_counter"
                " (tenant_id, scope_type, scope_key, open_run_count)"
                " VALUES (:tenant_id, :scope_type, :scope_key, :count)"
            ).bindparams(
                tenant_id=tenant_id, scope_type=scope_type, scope_key=scope_key, count=count
            )
        )
        await session.commit()


async def _read_policy(tenant_id: str) -> dict[str, Any] | None:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT source_settings_revision, max_open_runs_per_tenant,"
                    " max_open_runs_per_user FROM runtime.run_admission_policy"
                    " WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": tenant_id},
            )
        ).mappings().one_or_none()
    return None if row is None else dict(row)


async def _read_counter(tenant_id: str, *, scope_type: str, scope_key: str) -> int | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            sa.text(
                "SELECT open_run_count FROM runtime.run_admission_counter"
                " WHERE tenant_id = :tenant_id AND scope_type = :scope_type"
                " AND scope_key = :scope_key"
            ).bindparams(tenant_id=tenant_id, scope_type=scope_type, scope_key=scope_key)
        )


async def _count_open_runs(tenant_id: str) -> int:
    """在飞 Run 的实时计数：租户上限的判据（TENANT 计数行已不再维护）。"""
    async with get_session_factory()() as session:
        return int(
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(RunRecord)
                .where(
                    RunRecord.tenant_id == tenant_id,
                    RunRecord.admission_released_at.is_(None),
                )
            )
            or 0
        )


async def _count_rows(table: str, tenant_id: str, **filters: Any) -> int:
    clauses = " AND ".join(f"{column} = :{column}" for column in filters)
    where = f" WHERE tenant_id = :tenant_id AND {clauses}" if clauses else " WHERE tenant_id = :tenant_id"
    async with get_session_factory()() as session:
        return int(
            await session.scalar(
                sa.text(f"SELECT count(*) FROM runtime.{table}{where}").bindparams(
                    tenant_id=tenant_id, **filters
                )
            )
        )


async def _seed_conversation(
    tenant_id: str, *, user_id: uuid.UUID, agent_id: uuid.UUID
) -> uuid.UUID:
    conversation_id = uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO runtime.conversation (id, tenant_id, user_id, agent_id, status, last_seq)"
                " VALUES (:id, :tenant_id, :user_id, :agent_id, 'ACTIVE', 0)"
            ).bindparams(
                id=conversation_id, tenant_id=tenant_id, user_id=user_id, agent_id=agent_id
            )
        )
        await session.commit()
    return conversation_id


async def _wait_until_running(
    tenant_id: str, run_id: uuid.UUID, *, timeout: float = 15.0
) -> None:
    """受理在后台任务里领取执行：改状态前先等 Run 真正被领取（避免与领取竞态）。"""
    deadline = time.monotonic() + timeout
    while True:
        row = await _read_run_row(run_id)
        if row["status"] == "RUNNING":
            return
        if time.monotonic() > deadline:
            raise AssertionError(f"run {run_id} not claimed: {row['status']}")
        await asyncio.sleep(0.02)


async def _set_run_status(tenant_id: str, run_id: uuid.UUID, status: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.run_record SET status = :status"
                " WHERE tenant_id = :tenant_id AND id = :id"
            ).bindparams(tenant_id=tenant_id, id=run_id, status=status)
        )
        await session.commit()


async def _read_run_row(run_id: uuid.UUID) -> dict[str, Any] | None:
    async with get_session_factory()() as session:
        row = await session.get(RunRecord, run_id)
        if row is None:
            return None
        return {"status": str(row.status), "user_id": row.user_id, "tenant_id": row.tenant_id}


async def _read_frozen_policy(run_id: uuid.UUID) -> dict[str, Any]:
    async with get_session_factory()() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None and run.snapshot_id is not None
        snapshot = await session.get(RuntimeSnapshot, run.snapshot_id)
        assert snapshot is not None
        return dict(snapshot.policy_json)


# --------------------------------------------------------------------------------------
# Run 受理驱动：真实 RunService（替换的只有模型调用与定义解析）
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
    text: str = "admission create",
    bot_id: str = "bot-1",
    user_id: uuid.UUID | None = None,
    conversation_id: uuid.UUID | None = None,
) -> RunRequest:
    return RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=user_id or tenant.platform_user_id,
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
    key: str | None = None,
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


async def _drain(*starts: RunStart) -> None:
    for started in starts:
        async for _ in started.events:
            pass


# --------------------------------------------------------------------------------------
# B-230 断言 1/6：同事务提交 + 一份快照 + 返回前持久
# --------------------------------------------------------------------------------------


async def test_b230_create_run_commits_run_and_counts_in_one_transaction(
    admission_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-230] 受理返回时 Run/快照/提交/事件已持久，USER→TENANT 计数恰好各 +1。"""
    tenant = admission_tenant
    request = _request(tenant, text="persisted before return")
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=7,
                settings=_document(7, tenant_limit=4, user_limit=2, max_turns=7)["settings"],
            )
            started = await _start(
                tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                key="b230-commit-1", request=request,
            )
            # 返回时（尚未排空事件流）已持久：Run/快照/提交/事件都能被独立连接读到。
            run_row = await _read_run_row(started.run_id)
            assert run_row is not None, "受理返回前 Run 行必须已提交"
            assert run_row["user_id"] == tenant.platform_user_id
            assert await _count_rows("runtime_snapshot", tenant.tenant_id, run_id=started.run_id) == 1
            assert await _count_rows("run_submission", tenant.tenant_id, idempotency_key="b230-commit-1") == 1
            events = await _count_rows("canonical_event", tenant.tenant_id, run_id=started.run_id)
            assert events >= 2, "返回前 USER_MESSAGE/RUN_CREATED 必须已落库"
            # 受理与计数同事务：两个 scope 恰好各占 1
            assert await _read_counter(
                tenant.tenant_id, scope_type=SCOPE_USER, scope_key=str(tenant.platform_user_id)
            ) == 1
            assert await _count_open_runs(tenant.tenant_id) == 1
            # 同一份设置快照：阈值来自 rev 7，冻结策略也来自 rev 7（max_turns=7）
            assert await _read_policy(tenant.tenant_id) == {
                "source_settings_revision": 7,
                "max_open_runs_per_tenant": 4,
                "max_open_runs_per_user": 2,
            }
            assert (await _read_frozen_policy(started.run_id))["max_turns"] == 7
            gated_executor.release()
            await _drain(started)
        finally:
            await client.aclose()


async def test_b230_single_settings_snapshot_serves_admission_and_freeze(
    admission_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-230] 一次受理只取**一次**设置快照：准入阈值与冻结策略同源，不二次取配置。"""
    tenant = admission_tenant
    async with _scripted_source() as (source, url):
        source.script(
            tenant.tenant_id, _document(11, tenant_limit=4, user_limit=2, max_turns=11)
        )
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            started = await _start(
                tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                key="b230-snapshot-1",
            )
            assert source.arrivals == [tenant.tenant_id], "一次受理只应取一次设置快照"
            assert await _read_policy(tenant.tenant_id) == {
                "source_settings_revision": 11,
                "max_open_runs_per_tenant": 4,
                "max_open_runs_per_user": 2,
            }
            assert (await _read_frozen_policy(started.run_id))["max_turns"] == 11
            gated_executor.release()
            await _drain(started)
        finally:
            await client.aclose()


async def test_b230_schema_default_limits_apply_without_settings_version(
    admission_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-230] 无设置版本（revision 0 + 空文档）⇒ schema 默认上限生效：每用户 2 个非终态 Run。"""
    tenant = admission_tenant
    client = NullPlatformSettingsClient()
    first_conv = await _seed_conversation(
        tenant.tenant_id, user_id=tenant.platform_user_id, agent_id=tenant.agent_id
    )
    second_conv = await _seed_conversation(
        tenant.tenant_id, user_id=tenant.platform_user_id, agent_id=tenant.agent_id
    )
    third_conv = await _seed_conversation(
        tenant.tenant_id, user_id=tenant.platform_user_id, agent_id=tenant.agent_id
    )
    first = await _start(
        tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
        key="b230-default-1", request=_request(tenant, conversation_id=first_conv),
    )
    second = await _start(
        tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
        key="b230-default-2", request=_request(tenant, conversation_id=second_conv),
    )
    # 无版本行 ⇒ 阈值就是 schema 默认（每租户 2000 / 每用户 2），不是本地缓存或硬编码
    assert await _read_policy(tenant.tenant_id) == {
        "source_settings_revision": 0,
        "max_open_runs_per_tenant": 2000,
        "max_open_runs_per_user": 2,
    }
    with pytest.raises(AppError) as denied:
        await _start(
            tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
            key="b230-default-3", request=_request(tenant, conversation_id=third_conv),
        )
    assert denied.value.code == ErrorCode.RUN_CAPACITY_EXCEEDED
    assert await _read_counter(
        tenant.tenant_id, scope_type=SCOPE_USER, scope_key=str(tenant.platform_user_id)
    ) == 2
    assert await _count_rows("run_record", tenant.tenant_id) == 2
    gated_executor.release()
    await _drain(first, second)


async def test_b230_run_and_counts_commit_together_under_counter_lock(
    admission_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-230] 计数行被别的连接锁住时：Run 与计数都不可见；放行后一起出现（同事务）。"""
    tenant = admission_tenant
    user_key = str(tenant.platform_user_id)
    await _seed_setting(
        tenant.tenant_id,
        revision=3,
        settings=_document(3, tenant_limit=8, user_limit=5)["settings"],
    )
    await _seed_policy(tenant.tenant_id, revision=3, tenant_limit=8, user_limit=5)
    await _seed_counter(tenant.tenant_id, scope_type=SCOPE_USER, scope_key=user_key, count=0)
    engine: AsyncEngine = create_async_engine(SharedSettings().require_database_url())
    blocker = None
    transaction = None
    pending: asyncio.Task[RunStart] | None = None
    try:
        async with _console_source() as url:
            client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
            try:
                blocker = await engine.connect()
                transaction = await blocker.begin()
                await blocker.execute(
                    sa.text(
                        "SELECT open_run_count FROM runtime.run_admission_counter"
                        " WHERE tenant_id = :tenant_id AND scope_type = 'USER' AND scope_key = :key"
                        " FOR UPDATE"
                    ).bindparams(tenant_id=tenant.tenant_id, key=user_key)
                )
                pending = asyncio.create_task(
                    _start(
                        tenant, client, fake_resolve, gated_executor,
                        execution_supervisor_boundary, key="b230-tx-1",
                    )
                )
                await _wait_for_counter_waiter(engine)
                # 事务未提交：受理自增没有提前落库，Run 行也还不可见
                assert await _count_rows("run_record", tenant.tenant_id) == 0
                assert await _read_counter(
                    tenant.tenant_id, scope_type=SCOPE_USER, scope_key=user_key
                ) == 0
                await transaction.rollback()
                transaction = None
                await blocker.close()
                blocker = None
                started = await asyncio.wait_for(pending, timeout=WAIT_TIMEOUT_SEC)
                pending = None
                # 放行后两者一起出现：Run 行 + 计数各 1
                assert await _count_rows("run_record", tenant.tenant_id) == 1
                assert await _read_counter(
                    tenant.tenant_id, scope_type=SCOPE_USER, scope_key=user_key
                ) == 1
                assert await _count_open_runs(tenant.tenant_id) == 1
                gated_executor.release()
                await _drain(started)
            finally:
                await client.aclose()
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            with contextlib.suppress(BaseException):
                await pending
        if transaction is not None:
            with contextlib.suppress(Exception):
                await transaction.rollback()
        if blocker is not None:
            await blocker.close()
        await engine.dispose()


async def _wait_for_counter_waiter(engine: AsyncEngine) -> None:
    """等到受理事务真实阻塞在计数行锁上（`pg_stat_activity` 的 Lock 等待）。"""
    deadline = time.monotonic() + WAIT_TIMEOUT_SEC
    observed: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    sa.text(
                        "SELECT a.pid, a.state, a.wait_event_type, a.query"
                        " FROM pg_stat_activity a"
                        " WHERE a.state = 'active' AND a.query LIKE '%run_admission_counter%'"
                    )
                )
            ).mappings().all()
        observed = [dict(row) for row in rows]
        if any(row["wait_event_type"] == "Lock" for row in observed):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"超时未观察到受理事务在计数行锁上等待：最后观测={observed}")


# --------------------------------------------------------------------------------------
# B-230 断言 2/4：非终态占份额、达阈值整事务回滚
# --------------------------------------------------------------------------------------


async def test_b230_capacity_exceeded_rolls_back_without_fake_run(
    admission_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-230] 用户份额满：`RUN_CAPACITY_EXCEEDED` 且整事务回滚（不留半建 Run/会话/提交）。"""
    tenant = admission_tenant
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=2,
                settings=_document(2, tenant_limit=4, user_limit=2)["settings"],
            )
            first_conv = await _seed_conversation(
                tenant.tenant_id, user_id=tenant.platform_user_id, agent_id=tenant.agent_id
            )
            second_conv = await _seed_conversation(
                tenant.tenant_id, user_id=tenant.platform_user_id, agent_id=tenant.agent_id
            )
            first = await _start(
                tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                key="b230-limit-1", request=_request(tenant, conversation_id=first_conv),
            )
            second = await _start(
                tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                key="b230-limit-2", request=_request(tenant, conversation_id=second_conv),
            )
            assert await _read_counter(
                tenant.tenant_id, scope_type=SCOPE_USER, scope_key=str(tenant.platform_user_id)
            ) == 2
            runs_before = await _count_rows("run_record", tenant.tenant_id)
            # 满额后的新 key：给定第三个无活跃 Run 的会话，唯一能拒它的就是准入
            third_conv = await _seed_conversation(
                tenant.tenant_id, user_id=tenant.platform_user_id, agent_id=tenant.agent_id
            )
            conversations_before = await _count_rows("conversation", tenant.tenant_id)
            with pytest.raises(AppError) as denied:
                await _start(
                    tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                    key="b230-limit-3", request=_request(tenant, conversation_id=third_conv),
                )
            assert denied.value.code == ErrorCode.RUN_CAPACITY_EXCEEDED
            # 整事务回滚：无 Run、无提交记录、无事件，计数不变
            assert await _count_rows("run_record", tenant.tenant_id) == runs_before == 2
            assert await _count_rows("conversation", tenant.tenant_id) == conversations_before
            assert await _count_rows("canonical_event", tenant.tenant_id, conversation_id=third_conv) == 0
            assert await _count_rows("run_submission", tenant.tenant_id) == 2
            assert await _count_rows(
                "run_submission", tenant.tenant_id, idempotency_key="b230-limit-3"
            ) == 0
            assert await _read_counter(
                tenant.tenant_id, scope_type=SCOPE_USER, scope_key=str(tenant.platform_user_id)
            ) == 2
            assert await _count_open_runs(tenant.tenant_id) == 2
            # 满额的用户在新会话上同样被拒：请求自己新建的会话也随事务回滚，不留半建行
            blocked_user = uuid.uuid4()
            await _seed_counter(
                tenant.tenant_id, scope_type=SCOPE_USER, scope_key=str(blocked_user), count=2
            )
            with pytest.raises(AppError) as blocked:
                await _start(
                    tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                    key="b230-limit-4", request=_request(tenant, user_id=blocked_user),
                )
            assert blocked.value.code == ErrorCode.RUN_CAPACITY_EXCEEDED
            assert await _count_rows("run_record", tenant.tenant_id) == 2
            assert await _count_rows("conversation", tenant.tenant_id) == conversations_before
            assert await _count_rows("run_submission", tenant.tenant_id) == 2
            gated_executor.release()
            await _drain(first, second)
        finally:
            await client.aclose()


async def test_b230_tenant_share_is_shared_across_users(
    admission_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-230] 租户份额跨用户共享：他人占满后，本用户计数为 0 也被拒。"""
    tenant = admission_tenant
    user_b = uuid.uuid4()
    user_c = uuid.uuid4()
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=4,
                settings=_document(4, tenant_limit=2, user_limit=2)["settings"],
            )
            conv_a = await _seed_conversation(
                tenant.tenant_id, user_id=tenant.platform_user_id, agent_id=tenant.agent_id
            )
            conv_b = await _seed_conversation(
                tenant.tenant_id, user_id=user_b, agent_id=tenant.agent_id
            )
            first = await _start(
                tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                key="b230-tenant-1", request=_request(tenant, conversation_id=conv_a),
            )
            second = await _start(
                tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                key="b230-tenant-2",
                request=_request(tenant, user_id=user_b, conversation_id=conv_b),
            )
            assert await _count_open_runs(tenant.tenant_id) == 2
            with pytest.raises(AppError) as denied:
                await _start(
                    tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                    key="b230-tenant-3", request=_request(tenant, user_id=user_c),
                )
            assert denied.value.code == ErrorCode.RUN_CAPACITY_EXCEEDED
            assert await _read_counter(
                tenant.tenant_id, scope_type=SCOPE_USER, scope_key=str(user_c)
            ) is None, "被拒的请求不得留下自己的计数行"
            assert await _count_rows("run_record", tenant.tenant_id) == 2
            gated_executor.release()
            await _drain(first, second)
        finally:
            await client.aclose()


async def test_b230_non_terminal_runs_occupy_the_shared_share(
    admission_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-230] 全部非终态 Run 占份额：RUNNING/WAITING_INPUT/WAITING_TOOL 都不释放。"""
    tenant = admission_tenant
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=5,
                settings=_document(5, tenant_limit=4, user_limit=2)["settings"],
            )
            conv_one = await _seed_conversation(
                tenant.tenant_id, user_id=tenant.platform_user_id, agent_id=tenant.agent_id
            )
            conv_two = await _seed_conversation(
                tenant.tenant_id, user_id=tenant.platform_user_id, agent_id=tenant.agent_id
            )
            first = await _start(
                tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                key="b230-open-1", request=_request(tenant, conversation_id=conv_one),
            )
            second = await _start(
                tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                key="b230-open-2", request=_request(tenant, conversation_id=conv_two),
            )
            await _wait_until_running(tenant.tenant_id, first.run_id)
            await _wait_until_running(tenant.tenant_id, second.run_id)
            # 两个非终态 Run（RUNNING → WAITING_*）：份额仍被占用，第三个受理被拒
            await _set_run_status(tenant.tenant_id, first.run_id, "WAITING_INPUT")
            await _set_run_status(tenant.tenant_id, second.run_id, "WAITING_TOOL")
            assert (await _read_run_row(first.run_id))["status"] == "WAITING_INPUT"
            assert (await _read_run_row(second.run_id))["status"] == "WAITING_TOOL"
            third_conv = await _seed_conversation(
                tenant.tenant_id, user_id=tenant.platform_user_id, agent_id=tenant.agent_id
            )
            with pytest.raises(AppError) as denied:
                await _start(
                    tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                    key="b230-open-3", request=_request(tenant, conversation_id=third_conv),
                )
            assert denied.value.code == ErrorCode.RUN_CAPACITY_EXCEEDED
            assert await _read_counter(
                tenant.tenant_id, scope_type=SCOPE_USER, scope_key=str(tenant.platform_user_id)
            ) == 2
            assert await _count_open_runs(tenant.tenant_id) == 2
            # 断言完成：把两个 Run 还原为执行中再放行，让替身正常收尾（不留悬挂任务）
            await _set_run_status(tenant.tenant_id, first.run_id, "RUNNING")
            await _set_run_status(tenant.tenant_id, second.run_id, "RUNNING")
            gated_executor.release()
            await _drain(first, second)
        finally:
            await client.aclose()


# --------------------------------------------------------------------------------------
# B-230 断言 3/5：幂等重投不重复计数、指纹判别键
# --------------------------------------------------------------------------------------


async def test_b230_idempotent_replay_does_not_double_count(
    admission_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-230] 同 key 同指纹回放原 Run、不重复占位；份额满也不影响已受理的重放。"""
    tenant = admission_tenant
    request = _request(tenant, text="replay me")
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=6,
                settings=_document(6, tenant_limit=4, user_limit=2)["settings"],
            )
            first = await _start(
                tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                key="b230-replay-1", request=request,
            )
            replay = await _start(
                tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                key="b230-replay-1", request=request,
            )
            assert replay.run_id == first.run_id
            assert await _count_rows("run_record", tenant.tenant_id) == 1
            assert await _read_counter(
                tenant.tenant_id, scope_type=SCOPE_USER, scope_key=str(tenant.platform_user_id)
            ) == 1
            # 把用户份额占满（第二个新 key），再重放第一个 key：仍返回原 Run，不被准入拒
            conv = await _seed_conversation(
                tenant.tenant_id, user_id=tenant.platform_user_id, agent_id=tenant.agent_id
            )
            second = await _start(
                tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                key="b230-replay-2", request=_request(tenant, conversation_id=conv),
            )
            assert await _read_counter(
                tenant.tenant_id, scope_type=SCOPE_USER, scope_key=str(tenant.platform_user_id)
            ) == 2
            replay_again = await _start(
                tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                key="b230-replay-1", request=request,
            )
            assert replay_again.run_id == first.run_id
            assert await _count_rows("run_record", tenant.tenant_id) == 2
            assert await _read_counter(
                tenant.tenant_id, scope_type=SCOPE_USER, scope_key=str(tenant.platform_user_id)
            ) == 2
            # 新 key 在满额时被拒：准入只拦未受理的新提交
            third_conv = await _seed_conversation(
                tenant.tenant_id, user_id=tenant.platform_user_id, agent_id=tenant.agent_id
            )
            with pytest.raises(AppError) as denied:
                await _start(
                    tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                    key="b230-replay-3", request=_request(tenant, conversation_id=third_conv),
                )
            assert denied.value.code == ErrorCode.RUN_CAPACITY_EXCEEDED
            gated_executor.release()
            await _drain(first, replay, second, replay_again)
        finally:
            await client.aclose()


async def test_b230_fingerprint_covers_tenant_user_entry_and_conversation(
    admission_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-230] 指纹含 tenant/endpoint/user/entry/conversation：换任一判别键即冲突。"""
    tenant = admission_tenant
    other_tenant = TenantContext(
        tenant_id=f"{tenant.tenant_id}-other",
        agent_id=tenant.agent_id,
        platform_user_id=uuid.uuid4(),
    )
    base = _request(tenant, text="fingerprint base")
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=8,
                settings=_document(8, tenant_limit=8, user_limit=4)["settings"],
            )
            started = await _start(
                tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                key="b230-fp-1", request=base,
            )
            other_conv = await _seed_conversation(
                tenant.tenant_id, user_id=tenant.platform_user_id, agent_id=tenant.agent_id
            )
            mismatches = {
                "message": _request(tenant, text="changed text"),
                "entry": _request(tenant, text="fingerprint base", bot_id="bot-2"),
                "user": _request(tenant, text="fingerprint base", user_id=uuid.uuid4()),
                "conversation": _request(
                    tenant, text="fingerprint base", conversation_id=other_conv
                ),
            }
            for label, variant in mismatches.items():
                with pytest.raises(AppError) as conflicted:
                    await _start(
                        tenant, client, fake_resolve, gated_executor,
                        execution_supervisor_boundary, key="b230-fp-1", request=variant,
                    )
                assert conflicted.value.code == ErrorCode.IDEMPOTENCY_MISMATCH, label
            assert await _count_rows("run_record", tenant.tenant_id) == 1
            # 受理占位只在首次提交发生：冲突变体不新增 Run，也不新增计数
            assert await _read_counter(
                tenant.tenant_id, scope_type=SCOPE_USER, scope_key=str(tenant.platform_user_id)
            ) == 1
            # tenant 是幂等身份的一部分（不是冲突）：同 key 在别的租户是新提交
            other = await _start(
                other_tenant, client, fake_resolve, gated_executor,
                execution_supervisor_boundary, key="b230-fp-1", request=_request(other_tenant),
            )
            assert other.run_id != started.run_id
            assert await _read_counter(
                other_tenant.tenant_id, scope_type=SCOPE_USER,
                scope_key=str(other_tenant.platform_user_id),
            ) == 1
            gated_executor.release()
            await _drain(started, other)
        finally:
            await client.aclose()
            await _purge_tenant(other_tenant.tenant_id)


async def test_b230_settings_snapshot_is_fetched_outside_the_db_transaction(
    admission_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-230] 受理的 Console 材料在**写事务之外**取：取设置时本进程没有已检出的 DB 连接。"""
    from muad_agent_runtime.infrastructure.db import get_engine

    checked_out: list[int] = []

    class _PoolProbeSettings(ConsolePlatformSettingsClient):
        async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> Any:
            checked_out.append(get_engine().pool.checkedout())
            return await super().fetch_snapshot(tenant_id=tenant_id, trace_id=trace_id)

    tenant = admission_tenant
    async with _scripted_source() as (source, url):
        source.script(
            tenant.tenant_id, _document(11, tenant_limit=4, user_limit=2, max_turns=11)
        )
        client = _PoolProbeSettings(url, service_token=INTERNAL_TOKEN)
        try:
            started = await _start(
                tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                key="b230-outside-transaction",
            )
            assert checked_out == [0], "设置快照必须在写事务之外取（此时不应检出任何连接）"
            gated_executor.release()
            await _drain(started)
        finally:
            await client.aclose()


async def test_b230_stale_settings_revision_refetches_and_never_admits_on_old_thresholds(
    admission_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-230] 读到更低设置版本：退出事务重取一次；绝不按旧（更宽）阈值放行。"""
    tenant = admission_tenant
    user_key = str(tenant.platform_user_id)
    await _seed_policy(tenant.tenant_id, revision=9, tenant_limit=4, user_limit=2)
    await _seed_counter(tenant.tenant_id, scope_type=SCOPE_USER, scope_key=user_key, count=2)
    async with _scripted_source() as (source, url):
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            # 首次取到更宽的旧版本（rev 5），投影行已是 rev 9：必须重取，不按 rev 5 放行
            source.script(
                tenant.tenant_id,
                _document(5, tenant_limit=100, user_limit=50),
                _document(9, tenant_limit=4, user_limit=2),
            )
            with pytest.raises(AppError) as denied:
                await _start(
                    tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                    key="b230-stale-1",
                )
            assert denied.value.code == ErrorCode.RUN_CAPACITY_EXCEEDED
            assert source.arrivals == [tenant.tenant_id, tenant.tenant_id], "低版本必须退出事务重取一次"
            assert await _read_policy(tenant.tenant_id) == {
                "source_settings_revision": 9,
                "max_open_runs_per_tenant": 4,
                "max_open_runs_per_user": 2,
            }
            assert await _count_rows("run_record", tenant.tenant_id) == 0
            assert await _read_counter(tenant.tenant_id, scope_type=SCOPE_USER, scope_key=user_key) == 2
            # 重取仍低：显式失败，不建 Run、不改投影、不占位
            source.script(tenant.tenant_id, _document(5, tenant_limit=100, user_limit=50))
            arrivals_before = len(source.arrivals)
            with pytest.raises(AppError) as unavailable:
                await _start(
                    tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                    key="b230-stale-2",
                )
            assert unavailable.value.code == ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE
            assert len(source.arrivals) - arrivals_before == 2
            assert await _count_rows("run_record", tenant.tenant_id) == 0
            assert await _read_counter(tenant.tenant_id, scope_type=SCOPE_USER, scope_key=user_key) == 2
        finally:
            await client.aclose()


async def test_b230_stale_settings_revision_refreshes_and_admits_under_new_thresholds(
    admission_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-230] 读到更低版本：退出事务重取一次后按**新**阈值正常受理（既不超收也不误拒）。"""
    tenant = admission_tenant
    await _seed_policy(tenant.tenant_id, revision=9, tenant_limit=4, user_limit=2)
    async with _scripted_source() as (source, url):
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            source.script(
                tenant.tenant_id,
                _document(5, tenant_limit=100, user_limit=50),
                _document(9, tenant_limit=4, user_limit=2),
            )
            started = await _start(
                tenant, client, fake_resolve, gated_executor, execution_supervisor_boundary,
                key="b230-stale-admit",
            )
            assert source.arrivals == [tenant.tenant_id, tenant.tenant_id]
            assert await _read_policy(tenant.tenant_id) == {
                "source_settings_revision": 9,
                "max_open_runs_per_tenant": 4,
                "max_open_runs_per_user": 2,
            }
            assert await _read_counter(
                tenant.tenant_id, scope_type=SCOPE_USER, scope_key=str(tenant.platform_user_id)
            ) == 1
            assert await _count_rows("run_record", tenant.tenant_id) == 1
            gated_executor.release()
            await _drain(started)
        finally:
            await client.aclose()


# --------------------------------------------------------------------------------------
# B-230 断言 3：并发受理恰好收敛到阈值
# --------------------------------------------------------------------------------------


async def test_b230_concurrent_same_key_admits_exactly_one_run(
    admission_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-230] 并发同 key：advisory 串行化 + partial unique 兜底 ⇒ 恰好一个 Run、一次占位。"""
    tenant = admission_tenant
    request = _request(tenant, text="concurrent same key")
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=12,
                settings=_document(12, tenant_limit=8, user_limit=4)["settings"],
            )
            results = await asyncio.gather(
                *(
                    _start(
                        tenant, client, fake_resolve, gated_executor,
                        execution_supervisor_boundary, key="b230-concurrent-key", request=request,
                    )
                    for _ in range(2)
                ),
                return_exceptions=True,
            )
            failures = [item for item in results if isinstance(item, BaseException)]
            assert failures == [], f"并发同 key 不应失败：{failures}"
            run_ids = {item.run_id for item in results}
            assert len(run_ids) == 1, "并发同 key 必须回放同一个 Run"
            assert await _count_rows("run_record", tenant.tenant_id) == 1
            assert await _read_counter(
                tenant.tenant_id, scope_type=SCOPE_USER, scope_key=str(tenant.platform_user_id)
            ) == 1
            assert await _count_rows("run_submission", tenant.tenant_id) == 1
            gated_executor.release()
            await _drain(*results)
        finally:
            await client.aclose()


async def test_b230_concurrent_distinct_keys_converge_exactly_to_threshold(
    admission_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
) -> None:
    """[B-230] 并发新受理恰好收敛到阈值：限额 3 时 6 并发恰好 3 成 3 拒。"""
    tenant = admission_tenant
    async with _console_source() as url:
        client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
        try:
            await _seed_setting(
                tenant.tenant_id,
                revision=13,
                settings=_document(13, tenant_limit=4, user_limit=3)["settings"],
            )
            conversations = [
                await _seed_conversation(
                    tenant.tenant_id, user_id=tenant.platform_user_id, agent_id=tenant.agent_id
                )
                for _ in range(6)
            ]
            results = await asyncio.gather(
                *(
                    _start(
                        tenant, client, fake_resolve, gated_executor,
                        execution_supervisor_boundary, key=f"b230-race-{index}",
                        request=_request(tenant, conversation_id=conversation_id),
                    )
                    for index, conversation_id in enumerate(conversations)
                ),
                return_exceptions=True,
            )
            admitted = [item for item in results if isinstance(item, RunStart)]
            rejected = [item for item in results if isinstance(item, AppError)]
            other = [
                item
                for item in results
                if isinstance(item, BaseException) and not isinstance(item, AppError)
            ]
            assert other == [], f"并发受理出现非准入失败：{other}"
            assert len(admitted) == 3, f"限额 3 应恰好受理 3 个：{len(admitted)}"
            assert all(item.code == ErrorCode.RUN_CAPACITY_EXCEEDED for item in rejected)
            assert await _count_rows("run_record", tenant.tenant_id) == 3
            assert await _read_counter(
                tenant.tenant_id, scope_type=SCOPE_USER, scope_key=str(tenant.platform_user_id)
            ) == 3
            assert await _count_open_runs(tenant.tenant_id) == 3
            gated_executor.release()
            await _drain(*admitted)
        finally:
            await client.aclose()


async def test_s13_admission_returns_before_history_loader_finishes(
    admission_tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    gated_executor: _GatedExecutorFactory,
    execution_supervisor_boundary: ExecutionSupervisor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[S-13] 202 不等 history：历史加载可以在后台，但受理必须先返回并已持久。"""
    tenant = admission_tenant
    history_started = asyncio.Event()
    release_history = asyncio.Event()

    async def slow_history(self: RunService, *args: object, **kwargs: object) -> tuple[object, ...]:
        history_started.set()
        await release_history.wait()
        return ()

    monkeypatch.setattr(RunService, "_load_history", slow_history)
    started_at = time.monotonic()
    started = await _start(
        tenant,
        NullPlatformSettingsClient(),
        fake_resolve,
        gated_executor,
        execution_supervisor_boundary,
        key="s13-history-deferred",
    )
    elapsed = time.monotonic() - started_at
    assert elapsed < 2.0, f"受理等待了 history 加载：{elapsed:.2f}s"
    await asyncio.wait_for(history_started.wait(), timeout=5.0)
    assert await _read_run_row(started.run_id) is not None, "受理返回时 Run 必须已持久"
    release_history.set()
    gated_executor.release()
    await asyncio.wait_for(_drain(started), timeout=10.0)
