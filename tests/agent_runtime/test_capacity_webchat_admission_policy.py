"""B-229：[integration] 全局准入阈值的单调版本应用（design §3.2 / §3.7）。

真实边界（不 mock 存储或租约）：
- 真实 `RunAdmissionPolicyService`（`application/run_admission.py`）与 `RunAdmissionStore`
  （`infrastructure/run_admission_store.py`）；
- 隔离租户的真实 PostgreSQL：`runtime.run_admission_policy`、`runtime.run_admission_counter`、
  `runtime.conversation`、`runtime.run_record`（共享 dev 库只按 tenant 增删本任务的行）；
- 真实 Console 内部取设置端点（uvicorn + 真实 socket；Runtime 侧用真实
  `ConsolePlatformSettingsClient` 经真实 TCP 取快照，源为 `control.platform_setting` 版本行）；
- 脚本化设置探针（真实 HTTP，不是替身）：可返回更低版本、坏文档、失败状态码，并可在请求在途时
  挂起响应，用于观察「取配置 / 低版本刷新」期间没有任何行锁被持有。

关键断言（B-229 契约）：
1. `source_settings_revision` 只增不退：更低版本既不覆盖策略行也不放行；
2. policy 行锁先于会话行锁（真实 `pg_locks` + `FOR UPDATE NOWAIT` 探测）；
3. 旧实例不能按旧阈值超收：有效阈值取自 PG 投影行（更高版本）；
4. 降额不杀现有 Run：调低阈值不动 Run 与计数行，新请求等计数低于阈值才可受理；
5. 低版本网络刷新在事务外：刷新请求在途时策略行上没有本进程持有的锁。
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from muad_agent_runtime.application.run_admission import (
    AdmissionPolicyService,
    AdmissionPolicyStaleError,
    CapacityLimits,
    CapacitySnapshot,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.platform_settings_client import (
    PLATFORM_SETTINGS_PATH,
    ConsolePlatformSettingsClient,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_console_platform.infrastructure.db import get_session_factory as console_session_factory
from muad_console_platform.infrastructure.models.control import PlatformSetting
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction, create_async_engine

from agent_runtime.conftest import TenantContext

pytestmark = pytest.mark.integration

#: 内部服务身份：与 Console `require_service_identity` 同源（env `INTERNAL_SERVICE_TOKEN`）。
INTERNAL_TOKEN = "test-internal-token"
LISTEN_TIMEOUT_SEC = 15.0
WAIT_TIMEOUT_SEC = 30.0
#: 行锁被占的真实 PG 错误码（LockNotAvailable）。
LOCK_NOT_AVAILABLE = "55P03"
POLICY_LOCK_QUERY = (
    "SELECT id FROM runtime.run_admission_policy"
    " WHERE tenant_id = :tenant_id FOR UPDATE NOWAIT"
)
USER_SCOPE = "USER"
TENANT_SCOPE = "TENANT"


@pytest.fixture(autouse=True)
def _internal_service_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", INTERNAL_TOKEN)


@pytest.fixture
async def admission_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    """本套件独占的准入行：租户结束只删本租户的 policy/counter 与设置版本行。"""
    try:
        yield tenant
    finally:
        async with get_session_factory()() as session:
            for table in ("runtime.run_admission_counter", "runtime.run_admission_policy"):
                await session.execute(
                    sa.text(f"DELETE FROM {table} WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant.tenant_id},
                )
            await session.commit()
        async with console_session_factory()() as session:
            await session.execute(
                sa.text("DELETE FROM control.platform_setting WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant.tenant_id},
            )
            await session.commit()


# --------------------------------------------------------------------------------------
# 真实 HTTP 源：Console 内部端点 / 脚本化探针
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
class ScriptedSettingsSource:
    """真实 HTTP 设置源探针：脚本化返回 + 可在途挂起（客户端与 TCP 都是真的）。

    `script` 的最后一个文档反复生效；`hold_next()` 排一个闸门，第 N 个到达的请求会挂在闸门上，
    直到测试放行——用于把「取配置 / 低版本刷新」停在请求在途的那一刻。
    """

    arrivals: list[str]
    status: int
    app: FastAPI
    _scripts: dict[str, list[dict[str, Any]]]
    _gates: list[asyncio.Event]

    @classmethod
    def create(cls) -> ScriptedSettingsSource:
        source = cls(arrivals=[], status=200, app=FastAPI(), _scripts={}, _gates=[])
        source.app.get(PLATFORM_SETTINGS_PATH)(source._handle)
        return source

    def script(self, tenant_id: str, *documents: dict[str, Any]) -> None:
        assert documents, "至少给一个版本文档"
        self._scripts[tenant_id] = list(documents)

    def hold_next(self) -> asyncio.Event:
        gate = asyncio.Event()
        self._gates.append(gate)
        return gate

    async def _handle(self, request: Request) -> JSONResponse:
        tenant_id = request.headers.get("x-tenant-id", "")
        self.arrivals.append(tenant_id)
        if self._gates:
            await self._gates.pop(0).wait()
        if self.status >= 400:
            return JSONResponse(status_code=self.status, content={"code": "COMMON_INTERNAL_ERROR"})
        documents = self._scripts.get(tenant_id)
        if not documents:
            return JSONResponse({"code": "ok", "data": {"revision": 0, "settings": {}}})
        document = documents.pop(0) if len(documents) > 1 else documents[0]
        data = {"revision": document["revision"], "settings": {"capacity": document["capacity"]}}
        return JSONResponse({"code": "ok", "data": data})


@contextlib.asynccontextmanager
async def _probe_source() -> AsyncIterator[tuple[ScriptedSettingsSource, str]]:
    source = ScriptedSettingsSource.create()
    async with _serve(source.app) as url:
        yield source, url


@contextlib.asynccontextmanager
async def _service(url: str) -> AsyncIterator[AdmissionPolicyService]:
    """每个实例独立构造（无进程内缓存）：多实例语义用两个实例证明。"""
    client = ConsolePlatformSettingsClient(url, service_token=INTERNAL_TOKEN)
    try:
        yield AdmissionPolicyService(get_session_factory, client)
    finally:
        await client.aclose()


def _document(revision: int, *, tenant_limit: int, user_limit: int) -> dict[str, Any]:
    return {
        "revision": revision,
        "capacity": {
            "max_open_runs_per_tenant": tenant_limit,
            "max_open_runs_per_user": user_limit,
        },
    }


def _capacity(revision: int, *, tenant_limit: int, user_limit: int) -> CapacitySnapshot:
    return CapacitySnapshot(
        revision=revision,
        limits=CapacityLimits(
            max_open_runs_per_tenant=tenant_limit, max_open_runs_per_user=user_limit
        ),
    )


async def _wait_for_arrivals(source: ScriptedSettingsSource, count: int) -> None:
    deadline = time.monotonic() + WAIT_TIMEOUT_SEC
    while len(source.arrivals) < count:
        if time.monotonic() > deadline:
            raise AssertionError(f"设置源超时内只收到 {len(source.arrivals)} 次请求（期望 {count}）")
        await asyncio.sleep(0.02)


# --------------------------------------------------------------------------------------
# 真实 PG 行：设置版本、策略/计数、会话与 Run
# --------------------------------------------------------------------------------------


async def _seed_platform_setting(
    tenant_id: str, *, revision: int, tenant_limit: int, user_limit: int
) -> None:
    async with console_session_factory()() as session:
        session.add(
            PlatformSetting(
                tenant_id=tenant_id,
                revision=revision,
                settings_json={
                    "capacity": {
                        "max_open_runs_per_tenant": tenant_limit,
                        "max_open_runs_per_user": user_limit,
                    }
                },
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


async def _release_counters(tenant_id: str, *, released: int) -> None:
    """TASK-031 的终态释放语义：计数降下来（本任务只消费该事实）。"""
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.run_admission_counter"
                " SET open_run_count = open_run_count - :released WHERE tenant_id = :tenant_id"
            ).bindparams(tenant_id=tenant_id, released=released)
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


async def _seed_open_run(tenant: TenantContext, conversation_id: uuid.UUID) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO runtime.run_record (id, tenant_id, conversation_id, user_id, agent_id,"
                " status, input_text, trace_id, cancel_requested, lease_until, lease_owner)"
                " VALUES (:id, :tenant_id, :conversation_id, :user_id, :agent_id, 'RUNNING', 'x',"
                " :trace_id, false, :lease_until, 'rt-1')"
            ).bindparams(
                id=run_id,
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                trace_id=uuid.uuid4().hex,
                lease_until=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
        await session.commit()
    return run_id


async def _read_run(tenant_id: str, run_id: uuid.UUID) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT status, end_time, admission_released_at, queue_stage"
                    " FROM runtime.run_record WHERE tenant_id = :tenant_id AND id = :id"
                ),
                {"tenant_id": tenant_id, "id": run_id},
            )
        ).mappings().one()
    return dict(row)


async def _try_lock_policy_nowait(engine: AsyncEngine, tenant_id: str) -> str:
    """真实 PG：此刻策略行是否已被别的连接持有行锁（`FOR UPDATE NOWAIT`）。"""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(sa.text(POLICY_LOCK_QUERY), {"tenant_id": tenant_id})
            return "FREE"
        except sa.exc.DBAPIError as exc:
            assert getattr(exc.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE, f"非行锁冲突: {exc}"
            return "LOCKED"
        finally:
            with contextlib.suppress(Exception):
                await transaction.rollback()


async def _wait_for_waiter(engine: AsyncEngine, marker: str) -> int:
    """等到带标记的受理事务进入行锁等待，返回其真实后端 pid。"""
    deadline = time.monotonic() + WAIT_TIMEOUT_SEC
    observed: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    sa.text(
                        "SELECT a.pid, a.state, a.wait_event_type, a.wait_event"
                        " FROM pg_stat_activity a WHERE a.query LIKE :marker AND a.state = 'active'"
                    ).bindparams(marker=f"%{marker}%")
                )
            ).mappings().all()
        observed = [dict(row) for row in rows]
        waiting = [row for row in observed if row["wait_event_type"] == "Lock"]
        if waiting:
            return int(waiting[0]["pid"])
        await asyncio.sleep(0.05)
    raise AssertionError(f"超时未观察到受理事务在会话行锁上等待：最后观测={observed}")


async def _admit_then_lock_session(
    service: AdmissionPolicyService,
    tenant: TenantContext,
    conversation_id: uuid.UUID,
    capacity: CapacitySnapshot,
    marker: str,
) -> Any:
    """受理事务的锁定段：先应用策略版本（同类版本不写行），再取会话行锁等另一连接放行。"""
    async with get_session_factory()() as session:
        policy = await service.apply_policy_in(
            session, tenant_id=tenant.tenant_id, capacity=capacity
        )
        await session.execute(
            sa.text(
                "SELECT id FROM runtime.conversation WHERE tenant_id = :tenant_id AND id = :id"
                f" /* {marker} */ FOR UPDATE"
            ).bindparams(tenant_id=tenant.tenant_id, id=conversation_id)
        )
        await session.commit()
        return policy


# --------------------------------------------------------------------------------------
# B-229 断言 1：source_settings_revision 只增不退
# --------------------------------------------------------------------------------------


async def test_b229_policy_revision_only_increases(admission_tenant: TenantContext) -> None:
    """[B-229] 更高版本原地推进；更低版本既不覆盖策略行也不放行。"""
    tenant_id = admission_tenant.tenant_id
    async with _probe_source() as (source, url), _service(url) as service:
        source.script(tenant_id, _document(5, tenant_limit=4, user_limit=2))
        applied = await service.resolve(tenant_id=tenant_id)
        assert (applied.source_settings_revision, applied.limits) == (5, CapacityLimits(4, 2))

        # 更低版本（更宽阈值）：刷新一次后仍低 ⇒ 显式失败
        source.script(
            tenant_id, _document(3, tenant_limit=100, user_limit=50),
            _document(3, tenant_limit=100, user_limit=50),
        )
        with pytest.raises(AppError) as rejected:
            await service.resolve(tenant_id=tenant_id)
        assert rejected.value.code == ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE

        # 事务内原语同样拒绝低版本（调用方回滚），策略行保持不变
        async with get_session_factory()() as session:
            with pytest.raises(AdmissionPolicyStaleError):
                await service.apply_policy_in(
                    session, tenant_id=tenant_id, capacity=_capacity(3, tenant_limit=100, user_limit=50)
                )
            await session.rollback()
        assert await _read_policy(tenant_id) == {
            "source_settings_revision": 5,
            "max_open_runs_per_tenant": 4,
            "max_open_runs_per_user": 2,
        }, "更低版本不得覆盖更高版本投影"

        # 更高版本原地推进
        source.script(tenant_id, _document(9, tenant_limit=3, user_limit=2))
        advanced = await service.resolve(tenant_id=tenant_id)
        assert (advanced.source_settings_revision, advanced.limits) == (9, CapacityLimits(3, 2))
        assert await _read_policy(tenant_id) == {
            "source_settings_revision": 9,
            "max_open_runs_per_tenant": 3,
            "max_open_runs_per_user": 2,
        }


# --------------------------------------------------------------------------------------
# B-229 断言 1b：并发应用更高版本不回归（原子 upsert，无策略行锁）
# --------------------------------------------------------------------------------------


async def _apply_in_new_session(
    service: AdmissionPolicyService, tenant_id: str, capacity: CapacitySnapshot
) -> Any:
    async with get_session_factory()() as session:
        try:
            return await service.apply_policy_in(
                session, tenant_id=tenant_id, capacity=capacity
            )
        finally:
            await session.rollback()


async def _wait_for_blocked_policy_writer(engine: AsyncEngine) -> None:
    """等到某个事务的策略版本写入真实阻塞在行锁上（upsert 撞上未提交的更高版本）。"""
    deadline = time.monotonic() + WAIT_TIMEOUT_SEC
    while time.monotonic() < deadline:
        async with engine.connect() as connection:
            blocked = await connection.scalar(
                sa.text(
                    "SELECT pid FROM pg_stat_activity"
                    " WHERE datname = current_database()"
                    " AND state = 'active' AND wait_event_type = 'Lock'"
                    " AND query LIKE '%run_admission_policy%' LIMIT 1"
                )
            )
        if blocked is not None:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("超时未观察到策略版本写入被行锁阻塞")


async def test_b229_concurrent_lower_revision_never_overwrites_the_higher_one(
    admission_tenant: TenantContext,
) -> None:
    """[B-229] 并发应用：后到者拿着较低版本，必须 no-op 并显式 stale，绝不覆盖更高版本。

    判别点：去掉 upsert 的 `WHERE source_settings_revision < 新版本` 后，被阻塞的较低版本
    会覆盖更高版本（版本回退），本用例即失败。
    """
    tenant_id = admission_tenant.tenant_id
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with _probe_source() as (source, url), _service(url) as service:
            source.script(tenant_id, _document(5, tenant_limit=10, user_limit=5))
            await service.resolve(tenant_id=tenant_id)

            async with get_session_factory()() as session_a:
                applied = await service.apply_policy_in(
                    session_a,
                    tenant_id=tenant_id,
                    capacity=_capacity(7, tenant_limit=2, user_limit=2),
                )
                assert applied.source_settings_revision == 7
                lower = asyncio.create_task(
                    _apply_in_new_session(
                        service, tenant_id, _capacity(6, tenant_limit=100, user_limit=50)
                    )
                )
                try:
                    # 后到者读到的仍是未提交的 5，因 6 > 5 尝试写入并按行锁排队
                    await _wait_for_blocked_policy_writer(engine)
                    await session_a.commit()
                    with pytest.raises(AdmissionPolicyStaleError):
                        await asyncio.wait_for(lower, timeout=WAIT_TIMEOUT_SEC)
                finally:
                    if not lower.done():
                        lower.cancel()
                    with contextlib.suppress(BaseException):
                        await lower
            assert await _read_policy(tenant_id) == {
                "source_settings_revision": 7,
                "max_open_runs_per_tenant": 2,
                "max_open_runs_per_user": 2,
            }, "较低版本不得覆盖并发写入的更高版本"
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------------------
# B-229 断言 2：受理热路径不持有策略行锁
# --------------------------------------------------------------------------------------


async def test_b229_admission_does_not_hold_the_policy_row_lock(
    admission_tenant: TenantContext,
) -> None:
    """[B-229] 受理事务在等会话行锁期间，策略行仍可被另一事务 `FOR UPDATE NOWAIT` 取得。

    2026-10-20 起策略行不再于受理事务内加锁（原 `FOR UPDATE` 持有整个受理事务，把互不
    相关的用户串成一条队列）；更高版本改由单条原子 upsert 应用。本用例同时是反回归：
    重新在 `read_policy` 上加 `FOR UPDATE` 即失败。
    """
    tenant = admission_tenant
    marker = f"b229-accept-waiter-{uuid.uuid4().hex[:12]}"
    conversation_id = await _seed_conversation(
        tenant.tenant_id, user_id=tenant.platform_user_id, agent_id=tenant.agent_id
    )
    blocker_engine = create_async_engine(SharedSettings().require_database_url())
    observer_engine = create_async_engine(SharedSettings().require_database_url())
    blocker: AsyncConnection | None = None
    transaction: AsyncTransaction | None = None
    waiter: asyncio.Task[Any] | None = None
    try:
        async with _probe_source() as (source, url), _service(url) as service:
            source.script(tenant.tenant_id, _document(1, tenant_limit=2000, user_limit=2))
            await service.resolve(tenant_id=tenant.tenant_id)

            blocker = await blocker_engine.connect()
            transaction = await blocker.begin()
            await blocker.execute(
                sa.text(
                    "SELECT id FROM runtime.conversation"
                    " WHERE tenant_id = :tenant_id AND id = :id FOR UPDATE"
                ).bindparams(tenant_id=tenant.tenant_id, id=conversation_id)
            )
            # 受理事务：应用同一版本（不触发 upsert），再等会话行锁
            waiter = asyncio.create_task(
                _admit_then_lock_session(
                    service,
                    tenant,
                    conversation_id,
                    _capacity(1, tenant_limit=2000, user_limit=2),
                    marker,
                )
            )
            pid = await _wait_for_waiter(observer_engine, marker)
            assert pid > 0
            assert await _try_lock_policy_nowait(observer_engine, tenant.tenant_id) == "FREE", (
                "受理事务在等会话行锁时不得持有策略行锁（热路径无策略行锁）"
            )

            await transaction.rollback()
            policy = await asyncio.wait_for(waiter, timeout=WAIT_TIMEOUT_SEC)
            assert (policy.source_settings_revision, policy.limits) == (1, CapacityLimits(2000, 2))
            assert await _try_lock_policy_nowait(observer_engine, tenant.tenant_id) == "FREE"
    finally:
        if transaction is not None and transaction.is_active:
            with contextlib.suppress(Exception):
                await transaction.rollback()
        if waiter is not None and not waiter.done():
            waiter.cancel()
        if waiter is not None:
            await asyncio.gather(waiter, return_exceptions=True)
        if blocker is not None:
            with contextlib.suppress(Exception):
                await blocker.close()
        await blocker_engine.dispose()
        await observer_engine.dispose()


# --------------------------------------------------------------------------------------
# B-229 断言 3：旧实例不能按旧阈值超收
# --------------------------------------------------------------------------------------


async def test_b229_old_instance_cannot_over_admit_with_old_thresholds(
    admission_tenant: TenantContext,
) -> None:
    """[B-229] 有效阈值取自 PG 投影行：旧实例的低版本快照既不覆盖也不放行。"""
    tenant_id = admission_tenant.tenant_id
    user_id = admission_tenant.platform_user_id
    # 新实例经真实 Console 内部端点应用版本 9（阈值 2/2）
    async with _console_source() as console_url, _service(console_url) as new_instance:
        await _seed_platform_setting(tenant_id, revision=5, tenant_limit=10, user_limit=5)
        await _seed_platform_setting(tenant_id, revision=9, tenant_limit=2, user_limit=2)
        applied = await new_instance.resolve(tenant_id=tenant_id)
    assert (applied.source_settings_revision, applied.limits) == (9, CapacityLimits(2, 2))

    # 计数恰好到新阈值；旧阈值（10/5）此刻还会放行 —— 这是本用例的判别点
    await _seed_counter(tenant_id, scope_type=USER_SCOPE, scope_key=str(user_id), count=2)
    await _seed_counter(tenant_id, scope_type=TENANT_SCOPE, scope_key=tenant_id, count=2)

    async with _probe_source() as (source, url), _service(url) as old_instance:
        source.script(
            tenant_id, _document(5, tenant_limit=10, user_limit=5),
            _document(5, tenant_limit=10, user_limit=5),
        )
        with pytest.raises(AppError) as rejected:
            await old_instance.resolve(tenant_id=tenant_id)
        assert rejected.value.code == ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE
        assert await _read_policy(tenant_id) == {
            "source_settings_revision": 9,
            "max_open_runs_per_tenant": 2,
            "max_open_runs_per_user": 2,
        }, "旧实例不得把旧阈值写回投影行"

        async with get_session_factory()() as session:
            with pytest.raises(AdmissionPolicyStaleError):
                await old_instance.apply_policy_in(
                    session,
                    tenant_id=tenant_id,
                    capacity=_capacity(5, tenant_limit=10, user_limit=5),
                )
            await session.rollback()

    # 按投影行有效阈值（2/2）判定：计数已达阈值即拒；旧阈值 5 > 2 本会放行
    async with _console_source() as console_url, _service(console_url) as refreshed:
        effective = await refreshed.resolve(tenant_id=tenant_id)
        assert effective.limits == CapacityLimits(2, 2)
        async with get_session_factory()() as session:
            with pytest.raises(AppError) as denied:
                await refreshed.check_counts_in(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    policy=effective,
                )
            assert denied.value.code == ErrorCode.RUN_CAPACITY_EXCEEDED
            await session.rollback()


# --------------------------------------------------------------------------------------
# B-229 断言 4：降额不杀现有 Run，新请求等计数低于阈值
# --------------------------------------------------------------------------------------


async def test_b229_downgrade_keeps_open_runs_and_blocks_new_admission(
    admission_tenant: TenantContext,
) -> None:
    """[B-229] 阈值调低不动现有 Run/计数；新受理要等计数低于新阈值。"""
    tenant = admission_tenant
    # 同会话只允许一个活跃 Run：三条非终态 Run 分属三个会话，同属一个用户/租户
    run_ids: list[uuid.UUID] = []
    for _ in range(3):
        conversation_id = await _seed_conversation(
            tenant.tenant_id, user_id=tenant.platform_user_id, agent_id=tenant.agent_id
        )
        run_ids.append(await _seed_open_run(tenant, conversation_id))
    before = [await _read_run(tenant.tenant_id, run_id) for run_id in run_ids]

    async with _probe_source() as (source, url), _service(url) as service:
        source.script(tenant.tenant_id, _document(5, tenant_limit=10, user_limit=5))
        await service.resolve(tenant_id=tenant.tenant_id)
        await _seed_counter(
            tenant.tenant_id, scope_type=USER_SCOPE, scope_key=str(tenant.platform_user_id), count=3
        )
        await _seed_counter(
            tenant.tenant_id, scope_type=TENANT_SCOPE, scope_key=tenant.tenant_id, count=3
        )

        source.script(tenant.tenant_id, _document(9, tenant_limit=2, user_limit=2))
        downgraded = await service.resolve(tenant_id=tenant.tenant_id)
        assert downgraded.limits == CapacityLimits(2, 2)

        after = [await _read_run(tenant.tenant_id, run_id) for run_id in run_ids]
        assert after == before, "降额不得改动已有 Run 行"
        assert all(
            row["status"] == "RUNNING" and row["admission_released_at"] is None for row in after
        )
        assert await _read_counter(
            tenant.tenant_id, scope_type=USER_SCOPE, scope_key=str(tenant.platform_user_id)
        ) == 3
        assert await _read_counter(
            tenant.tenant_id, scope_type=TENANT_SCOPE, scope_key=tenant.tenant_id
        ) == 3

        async with get_session_factory()() as session:
            with pytest.raises(AppError) as denied:
                await service.check_counts_in(
                    session,
                    tenant_id=tenant.tenant_id,
                    user_id=tenant.platform_user_id,
                    policy=downgraded,
                )
            assert denied.value.code == ErrorCode.RUN_CAPACITY_EXCEEDED
            await session.rollback()

        # 终态释放使计数低于阈值后，新受理才放行
        await _release_counters(tenant.tenant_id, released=2)
        async with get_session_factory()() as session:
            await service.check_counts_in(
                session,
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                policy=downgraded,
            )
            await session.rollback()


# --------------------------------------------------------------------------------------
# B-229 断言 5：低版本网络刷新在事务外；坏源显式失败
# --------------------------------------------------------------------------------------


async def test_b229_stale_refresh_happens_outside_any_transaction(
    admission_tenant: TenantContext,
) -> None:
    """[B-229] 取配置与低版本刷新都在事务外：请求在途时策略行上没有行锁。"""
    tenant_id = admission_tenant.tenant_id
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with _probe_source() as (source, url), _service(url) as service:
            source.script(tenant_id, _document(5, tenant_limit=4, user_limit=2))
            await service.resolve(tenant_id=tenant_id)

            source.script(
                tenant_id, _document(3, tenant_limit=100, user_limit=50),
                _document(5, tenant_limit=4, user_limit=2),
            )
            gates = [source.hold_next(), source.hold_next()]
            task = asyncio.create_task(service.resolve(tenant_id=tenant_id))
            try:
                await _wait_for_arrivals(source, 2)
                assert await _try_lock_policy_nowait(engine, tenant_id) == "FREE", (
                    "取配置在事务外，不得持有策略行锁"
                )
                gates[0].set()
                await _wait_for_arrivals(source, 3)
                assert await _try_lock_policy_nowait(engine, tenant_id) == "FREE", (
                    "低版本刷新必须在退出事务之后（刷新在途不持有行锁）"
                )
                gates[1].set()
                resolved = await asyncio.wait_for(task, timeout=WAIT_TIMEOUT_SEC)
            finally:
                # 任何断言失败都要放行在途请求并回收任务，避免把探针服务卡在响应上。
                for gate in gates:
                    gate.set()
                if not task.done():
                    task.cancel()
                with contextlib.suppress(BaseException):
                    await task
            assert resolved.source_settings_revision == 5
            assert len(source.arrivals) == 3, "低版本只重取一次，绝不覆盖更高版本"
    finally:
        await engine.dispose()


async def test_b229_bad_source_never_falls_back_to_defaults(
    admission_tenant: TenantContext,
) -> None:
    """[B-229] 源失败/坏文档显式失败：不建默认投影、不覆盖上一版本。"""
    tenant_id = admission_tenant.tenant_id
    async with _probe_source() as (source, url), _service(url) as service:
        # 无历史行的租户 + 源失败：不得建立任何「默认值」投影行
        source.status = 500
        with pytest.raises(AppError) as failed:
            await service.resolve(tenant_id=tenant_id)
        assert failed.value.code == ErrorCode.COMMON_INTERNAL_ERROR
        assert await _read_policy(tenant_id) is None

        # 先建立版本 5，再遇到坏文档（阈值越界）仍不得回落 schema 默认（2000/2）
        source.status = 200
        source.script(tenant_id, _document(5, tenant_limit=4, user_limit=2))
        await service.resolve(tenant_id=tenant_id)
        source.script(
            tenant_id,
            _document(9, tenant_limit=1, user_limit=1),
        )
        with pytest.raises(AppError) as invalid:
            await service.resolve(tenant_id=tenant_id)
        assert invalid.value.code == ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE
        assert await _read_policy(tenant_id) == {
            "source_settings_revision": 5,
            "max_open_runs_per_tenant": 4,
            "max_open_runs_per_user": 2,
        }
