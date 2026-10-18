"""B-235：[integration] PG 时钟、租约及绝对期限校验（design §3.5 时间 / §3.7 配置契约）。

真实边界（不 mock 存储或租约）：
- 真实 `RunService`/`claim_continuation`/`renew_execution`/`expire_runs`/`reap_abandoned_runs`
  业务路径；真实 `ModelCapacityService`（真实 `control.model_credential` 行与
  `runtime.model_capacity`/`model_call_lease` 行）；真实 `WorkerLoop`/`TaskClaimer`/
  `DeadlineSweeper`；
- 隔离 PostgreSQL（空库 + `alembic upgrade head`，跑完即 drop）：运行侧用
  `async_tool_database`，Worker 侧把 `DATABASE_URL` 指到同一隔离库，不触碰共享 dev 库；
- **进程钟与 PG 钟的偏差是显式的**：`_freeze_process_clock` 把被测模块的本机墙钟固定到
  ±小时级时刻，而 PG 时钟不动 —— 裁决结果只允许跟着 PG 走（本机时钟偏差不延长执行权）。

关键断言（B-235 契约）：
1. 受理冻结的绝对期限/排队窗口来自 PG 时钟（`deadline_at - queued_at` = 冻结的 deadline_ms，
   `queue_deadline_at = min(受理 + 队列等待配置, 绝对期限)`，受理时刻落在 PG now 附近）；
2. 领取守卫按 PG 时钟：PG 未过期（进程钟拨快）仍可领取且新租约 = PG now + lease_sec；
   PG 已过期（进程钟拨慢）拒绝领取，超时后不得调用模型；
3. Run 心跳续租按 PG 时钟：PG 有效即续（期限只来自 PG），PG 失约/绝对期限已过即显式拒绝；
4. 绝对期限扫描按 PG 时钟收尾：进程钟拨快不误杀、进程钟拨慢不放过，`end_time` 用 PG 时间；
5. Reaper 回收过期租约按 PG 时钟：进程钟拨慢也照常回收（CAS 不是本机时钟），拨快不碰有效租约；
6. 模型调用租约与窗口的时钟源就是 PG：预占期限/续租/失约全部按 PG 判定，进程钟偏差不影响；
7. Worker 租约续租与结果期限判定按 PG 时钟：失约显式 `(False, _)` 停手、超期不写 COMPLETED；
8. Task 领取与 deadline sweep 按 PG 时钟：PG 过期不执行、PG 未过期不误杀，收尾时间用 PG。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application import model_capacity_service, run_deadline, run_service
from muad_agent_runtime.application.async_tools import continuation_service
from muad_agent_runtime.application.async_tools.continuation_service import (
    ExecutionIdentity,
    claim_continuation,
    renew_execution,
)
from muad_agent_runtime.application.model_capacity_service import ModelCapacityService
from muad_agent_runtime.application.ports import NullPlatformSettingsClient, PlatformSettingsSnapshot
from muad_agent_runtime.application.run_service import RunService, reap_abandoned_runs
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import RunRecord, RunSubmission
from muad_agent_worker.infrastructure import db as worker_db
from muad_agent_worker.infrastructure.db import get_session_factory as worker_session_factory
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.scheduler import service as scheduler_service
from muad_agent_worker.scheduler.service import (
    TASK_DEADLINE_EXCEEDED,
    DeadlineSweeper,
    SchedulerLoop,
    ScheduleService,
)
from muad_agent_worker.worker import claimer as claimer_module
from muad_agent_worker.worker import service as worker_service
from muad_agent_worker.worker.claimer import TaskClaimer
from muad_agent_worker.worker.service import WorkerLoop
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_contracts import (
    ChannelContext,
    CreateScheduleRequest,
    DeliveryRouteInput,
    MessageInput,
    ResolveDefinitionResponse,
    RunRequest,
    RunStatus,
    ScheduleSpec,
)
from muad_contracts.model_pool import ModelCapacityReservationRequest, ProbeCallOwner
from muad_contracts.platform_settings import default_platform_settings
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from agent_runtime.conftest import FakeResolveClient, TenantContext
from tests.async_tool_submissions import seed_submission

pytestmark = pytest.mark.integration

INSTANCE_ID = "b235-clock-instance"
#: `runtime.run_record.queue_stage` 的排队态取值（与表级 CHECK 同源）。
QUEUE_STAGE = "WAITING_RUNTIME"
#: 进程钟与 PG 钟的显式偏差：拨快 6 小时 / 拨慢 1 小时。
SKEW_FAST = timedelta(hours=6)
SKEW_SLOW = timedelta(hours=1)
#: PG 时间与测试断言之间的容许误差（真实事务耗时，不是裁决口径）。
CLOCK_TOLERANCE = timedelta(seconds=60)
SNAPSHOT_HASH = "sha256:" + "c" * 64
RUN_ABANDONED = "RUN_ABANDONED"
RUN_DEADLINE_EXCEEDED = "RUN_DEADLINE_EXCEEDED"


# --------------------------------------------------------------------------------------
# 时钟证据工具：真实 PG 参照 + 显式的进程钟偏差
# --------------------------------------------------------------------------------------


async def _pg_now(source: AsyncEngine | async_sessionmaker[AsyncSession]) -> datetime:
    """真实 PG 服务端时间（`now()`，事务起点）：所有期限断言的时间参照。"""
    if isinstance(source, AsyncEngine):
        async with source.connect() as connection:
            value = await connection.scalar(sa.select(sa.func.now()))
    else:
        async with source() as session:
            value = await session.scalar(sa.select(sa.func.now()))
    assert isinstance(value, datetime), "PG 服务端时钟必须可用"
    return value


def _freeze_process_clock(
    monkeypatch: pytest.MonkeyPatch, module: Any, moment: datetime
) -> None:
    """把模块级本机墙钟固定到 `moment`（PG 时钟不动）：偏差下的一致性证据。"""
    assert moment.tzinfo is not None, "偏差时刻必须带时区"

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return moment if tz is not None else moment.astimezone(UTC).replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", _FrozenDatetime)


# --------------------------------------------------------------------------------------
# 真实来源：隔离库 fixture、受理请求与设置源
# --------------------------------------------------------------------------------------


@pytest.fixture
async def clock_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    """受理路径的租户：额外清掉准入投影/计数行（只清本租户）。"""
    try:
        yield tenant
    finally:
        async with get_session_factory()() as session:
            for table in ("run_admission_policy", "run_admission_counter"):
                await session.execute(
                    sa.text(f"DELETE FROM runtime.{table} WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant.tenant_id},
                )
            await session.commit()


@pytest.fixture
def runtime_factory(async_tool_database: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(async_tool_database, expire_on_commit=False)


@pytest.fixture
async def worker_factory(
    async_tool_datastore: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Worker 侧指向隔离 PostgreSQL/Redis：跑完 drop，不触碰共享 dev 库。"""
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "b235-internal-token")
    await worker_db.dispose_engine()
    try:
        yield worker_session_factory()
    finally:
        await worker_db.dispose_engine()


class _CapacitySettingsClient:
    """静态设置源：revision 0 + 显式 capacity 组（队列等待窗口 60 秒）。"""

    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(
            revision=0,
            settings={
                "capacity": {
                    "max_open_runs_per_tenant": 100_000,
                    "max_open_runs_per_user": 100,
                    "queue_wait_timeout_sec": 60,
                }
            },
        )


def _request(tenant: TenantContext, *, text: str = "clock probe") -> RunRequest:
    return RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=tenant.platform_user_id,
        channel=ChannelContext(type="WECOM", bot_id=f"bot-{uuid.uuid4().hex[:8]}"),
        message=MessageInput(id=f"msg-{uuid.uuid4()}", text=text),
    )


def _short_deadline_resolve(
    resolved: ResolveDefinitionResponse, *, deadline_ms: int
) -> ResolveDefinitionResponse:
    agent = resolved.agent.model_copy(update={"runtime_config": {"deadline_ms": deadline_ms}})
    return resolved.model_copy(update={"agent": agent})


async def _never_claimed_executor(request: Any) -> Any:
    raise AssertionError("本用例的 Run 绝不该被领取（期限已过）")


# --------------------------------------------------------------------------------------
# 真实 PG 行：Run 的排队窗口/租约/绝对期限
# --------------------------------------------------------------------------------------


async def _seed_queued_run(factory: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    """真实受理行（快照 + 会话）+ 真实 OPEN 提交：可领取的初始排队 Run。"""
    _context, _skill, run = await seed_submission(factory)
    async with factory() as session, session.begin():
        row = await session.get(RunRecord, run.id)
        assert row is not None
        row.status, row.execution_epoch = RunStatus.CREATED, 0
        row.lease_owner = row.lease_until = None
        row.queue_stage = QUEUE_STAGE
        row.queued_at = row.queue_deadline_at = row.deadline_at = None
        session.add(
            RunSubmission(
                tenant_id=run.tenant_id,
                run_id=run.id,
                conversation_id=run.conversation_id,
                idempotency_key=str(uuid.uuid4()),
                endpoint="create-run",
                request_fingerprint="sha256:" + "c" * 64,
                actor_user_id=run.user_id,
            )
        )
    return run.id


async def _set_run_window(
    factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    *,
    queued_at: datetime,
    queue_deadline_at: datetime,
    deadline_at: datetime,
) -> None:
    async with factory() as session, session.begin():
        row = await session.get(RunRecord, run_id)
        assert row is not None
        row.queued_at, row.queue_deadline_at, row.deadline_at = queued_at, queue_deadline_at, deadline_at


async def _set_run_lease(
    factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    *,
    lease_owner: str | None = INSTANCE_ID,
    epoch: int = 1,
    lease_until: datetime | None,
    deadline_at: datetime | None,
) -> None:
    async with factory() as session, session.begin():
        row = await session.get(RunRecord, run_id)
        assert row is not None
        row.status, row.execution_epoch = RunStatus.RUNNING, epoch
        row.lease_owner, row.lease_until, row.deadline_at = lease_owner, lease_until, deadline_at


@dataclass(frozen=True, slots=True)
class _RunState:
    status: str
    lease_owner: str | None
    lease_until: datetime | None
    execution_epoch: int
    error_code: str | None
    end_time: datetime | None


async def _run_state(factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> _RunState:
    async with factory() as session:
        row = await session.get(RunRecord, run_id)
        assert row is not None, f"run {run_id} must exist"
        return _RunState(
            str(row.status),
            row.lease_owner,
            row.lease_until,
            int(row.execution_epoch),
            row.error_code,
            row.end_time,
        )


# --------------------------------------------------------------------------------------
# 1. 受理冻结的绝对期限与排队窗口来自 PG 时钟
# --------------------------------------------------------------------------------------


async def test_b235_run_creation_freezes_deadline_and_queue_window_on_pg_clock(
    clock_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    execution_supervisor_boundary: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[B-235] 进程钟拨慢 1 小时：`deadline_at`/`queued_at`/`queue_deadline_at` 仍按 PG 冻结。"""
    pg_before = await _pg_now(get_session_factory())
    _freeze_process_clock(monkeypatch, run_service, pg_before - SKEW_SLOW)
    async with get_session_factory()() as session:
        service = RunService(
            session,
            FakeResolveClient(response=_short_deadline_resolve(resolved, deadline_ms=1)),
            INSTANCE_ID,
            _CapacitySettingsClient(),
            executor_factory=_never_claimed_executor,
            supervisor=execution_supervisor_boundary,
        )
        started = await service.start(
            _request(clock_tenant), clock_tenant.tenant_id, idempotency_key="b235-clock-freeze"
        )
    pg_after = await _pg_now(get_session_factory())
    async with get_session_factory()() as session:
        row = await session.get(RunRecord, started.run_id)
        assert row is not None
        deadline_at, queued_at = row.deadline_at, row.queued_at
        queue_deadline_at, start_time, status = row.queue_deadline_at, row.start_time, str(row.status)
        lease_owner = row.lease_owner
    assert deadline_at is not None and queued_at is not None and queue_deadline_at is not None
    # 同一次 PG now() 冻结：期限差恰是冻结的 deadline_ms；排队窗口 = min(受理+60s, 绝对期限)
    assert deadline_at - queued_at == timedelta(milliseconds=1)
    assert queue_deadline_at == deadline_at
    assert start_time == queued_at
    # 进程钟被拨慢 1 小时：受理时刻必须仍落在 PG 时钟附近（权威是 PG，不是本机墙钟）
    assert abs(queued_at - pg_after) <= CLOCK_TOLERANCE
    assert abs(deadline_at - pg_after) <= CLOCK_TOLERANCE
    assert status == "CREATED" and lease_owner is None, "期限已过：不得被领取"


# --------------------------------------------------------------------------------------
# 2. 领取守卫按 PG 时钟（不是进程 UTC）
# --------------------------------------------------------------------------------------


async def test_b235_claim_guard_follows_pg_clock_not_process_clock(
    runtime_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-235] 进程钟拨快不放弃可领取的排队 Run；拨慢不放行 PG 已过期的排队 Run。"""
    factory = runtime_factory
    # A) PG 未过期 + 进程钟拨快 6 小时：领取按 PG 事实放行，新租约期限来自 PG
    run_a = await _seed_queued_run(factory)
    pg_a = await _pg_now(factory)
    await _set_run_window(
        factory,
        run_a,
        queued_at=pg_a - timedelta(seconds=5),
        queue_deadline_at=pg_a + timedelta(seconds=30),
        deadline_at=pg_a + timedelta(seconds=120),
    )
    with monkeypatch.context() as patch:
        _freeze_process_clock(patch, continuation_service, pg_a + SKEW_FAST)
        claimed = await claim_continuation(
            factory, instance_id=INSTANCE_ID, lease_sec=30, run_id=run_a
        )
    assert claimed is not None and claimed.id == run_a
    assert claimed.status == RunStatus.RUNNING and claimed.execution_epoch == 1
    assert claimed.queue_stage is None and claimed.queue_reason is None
    assert claimed.lease_until is not None
    assert claimed.lease_until <= pg_a + timedelta(seconds=30) + CLOCK_TOLERANCE
    assert claimed.lease_until < pg_a + SKEW_FAST, "租约期限不得跟随进程钟"
    # B) PG 已过期 + 进程钟拨慢 1 小时：拒绝领取（超时后不得再调用模型）
    run_b = await _seed_queued_run(factory)
    pg_b = await _pg_now(factory)
    await _set_run_window(
        factory,
        run_b,
        queued_at=pg_b - timedelta(seconds=60),
        queue_deadline_at=pg_b - timedelta(seconds=1),
        deadline_at=pg_b + timedelta(seconds=120),
    )
    with monkeypatch.context() as patch:
        _freeze_process_clock(patch, continuation_service, pg_b - SKEW_SLOW)
        refused = await claim_continuation(
            factory, instance_id=INSTANCE_ID, lease_sec=30, run_id=run_b
        )
    assert refused is None
    state = await _run_state(factory, run_b)
    assert state.status == "CREATED" and state.lease_owner is None and state.execution_epoch == 0


# --------------------------------------------------------------------------------------
# 3. Run 心跳续租按 PG 时钟
# --------------------------------------------------------------------------------------


async def test_b235_run_heartbeat_renewal_follows_pg_clock(
    runtime_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-235] PG 有效即续（期限来自 PG）；PG 失约或绝对期限已过即显式拒绝。"""
    factory = runtime_factory
    _context, _skill, run = await seed_submission(factory)
    pg = await _pg_now(factory)
    identity = ExecutionIdentity(run.tenant_id, run.id, INSTANCE_ID, 1)
    await _set_run_lease(
        factory,
        run.id,
        lease_until=pg + timedelta(seconds=300),
        deadline_at=pg + timedelta(seconds=600),
    )
    # A) PG 租约有效 + 进程钟拨快 6 小时：续租成功，新期限 = PG now + lease_sec
    with monkeypatch.context() as patch:
        _freeze_process_clock(patch, continuation_service, pg + SKEW_FAST)
        assert await renew_execution(factory, identity, lease_sec=60) is True
    renewed = await _run_state(factory, run.id)
    assert renewed.lease_until is not None
    assert renewed.lease_until <= pg + timedelta(seconds=60) + CLOCK_TOLERANCE
    assert renewed.lease_until < pg + SKEW_FAST, "进程钟偏差不得延长执行权"
    # B) PG 租约已过期 + 进程钟拨慢 1 小时：显式失约，不延长
    await _set_run_lease(
        factory,
        run.id,
        lease_until=pg - timedelta(seconds=1),
        deadline_at=pg + timedelta(seconds=600),
    )
    with monkeypatch.context() as patch:
        _freeze_process_clock(patch, continuation_service, pg - SKEW_SLOW)
        assert await renew_execution(factory, identity, lease_sec=60) is False
    expired = await _run_state(factory, run.id)
    assert expired.lease_until == pg - timedelta(seconds=1), "失约时不得改写租约期限"
    # C) 绝对期限已过 + 进程钟拨慢 1 小时：同样拒绝（期限只由 PG 裁决）
    await _set_run_lease(
        factory,
        run.id,
        lease_until=pg + timedelta(seconds=300),
        deadline_at=pg - timedelta(seconds=1),
    )
    with monkeypatch.context() as patch:
        _freeze_process_clock(patch, continuation_service, pg - SKEW_SLOW)
        assert await renew_execution(factory, identity, lease_sec=60) is False


# --------------------------------------------------------------------------------------
# 4. 绝对期限扫描按 PG 时钟收尾
# --------------------------------------------------------------------------------------


async def test_b235_absolute_deadline_sweep_follows_pg_clock(
    runtime_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-235] 进程钟拨快不误杀、拨慢不放过；`end_time` 用 PG 时间。"""
    factory = runtime_factory
    _context, _skill, run = await seed_submission(factory)
    pg = await _pg_now(factory)
    await _set_run_lease(
        factory, run.id, lease_until=pg + timedelta(seconds=300), deadline_at=pg + timedelta(seconds=600)
    )
    with monkeypatch.context() as patch:
        _freeze_process_clock(patch, run_deadline, pg + SKEW_FAST)
        assert await run_deadline.expire_runs(factory) == 0
    assert (await _run_state(factory, run.id)).status == "RUNNING"
    await _set_run_lease(
        factory, run.id, lease_until=pg + timedelta(seconds=300), deadline_at=pg - timedelta(seconds=1)
    )
    with monkeypatch.context() as patch:
        _freeze_process_clock(patch, run_deadline, pg - SKEW_SLOW)
        assert await run_deadline.expire_runs(factory) == 1
    expired = await _run_state(factory, run.id)
    assert expired.status == "FAILED" and expired.error_code == RUN_DEADLINE_EXCEEDED
    assert expired.lease_owner is None and expired.lease_until is None
    assert expired.end_time is not None and abs(expired.end_time - pg) <= CLOCK_TOLERANCE


# --------------------------------------------------------------------------------------
# 5. Reaper 回收过期租约按 PG 时钟
# --------------------------------------------------------------------------------------


async def test_b235_reaper_reclaims_pg_expired_lease_under_skewed_process_clock(
    runtime_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-235] 进程钟拨快不碰有效租约；拨慢也照常回收（回收 CAS 不是本机时钟）。"""
    factory = runtime_factory
    _context, _skill, run = await seed_submission(factory)
    pg = await _pg_now(factory)
    await _set_run_lease(
        factory, run.id, lease_until=pg + timedelta(seconds=300), deadline_at=pg + timedelta(seconds=600)
    )
    with monkeypatch.context() as patch:
        patch.setattr(run_service, "_utcnow", lambda: pg + SKEW_FAST)
        assert await reap_abandoned_runs(factory) == 0
    assert (await _run_state(factory, run.id)).status == "RUNNING"
    await _set_run_lease(
        factory, run.id, lease_until=pg - timedelta(seconds=1), deadline_at=pg + timedelta(seconds=600)
    )
    with monkeypatch.context() as patch:
        patch.setattr(run_service, "_utcnow", lambda: pg - SKEW_SLOW)
        assert await reap_abandoned_runs(factory) == 1
    reaped = await _run_state(factory, run.id)
    assert reaped.status == "FAILED" and reaped.error_code == RUN_ABANDONED
    assert reaped.lease_owner is None and reaped.lease_until is None
    assert reaped.end_time is not None and abs(reaped.end_time - pg) <= CLOCK_TOLERANCE


# --------------------------------------------------------------------------------------
# 6. 模型调用租约与窗口的时钟源就是 PG
# --------------------------------------------------------------------------------------


async def _seed_credential(
    factory: async_sessionmaker[AsyncSession], tenant_id: str, *, max_inflight: int
) -> uuid.UUID:
    credential_id = uuid.uuid4()
    async with factory() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO control.model_credential (id, tenant_id, key, provider_key, base_url, api_key,"
                " api_key_digest, quota_group_id, max_inflight, enabled, revision)"
                " VALUES (:id, :tenant_id, :key, 'openai', 'https://api.example.com/v1', :api_key,"
                " :digest, NULL, :max_inflight, true, 1)"
            ).bindparams(
                id=credential_id,
                tenant_id=tenant_id,
                key=f"key-{uuid.uuid4().hex[:12]}",
                api_key=f"sk-test-{uuid.uuid4().hex}",
                digest=uuid.uuid4().hex + uuid.uuid4().hex,
                max_inflight=max_inflight,
            )
        )
    return credential_id


async def _expire_lease(
    factory: async_sessionmaker[AsyncSession], tenant_id: str, attempt_id: uuid.UUID, moment: datetime
) -> None:
    async with factory() as session, session.begin():
        await session.execute(
            sa.text(
                "UPDATE runtime.model_call_lease SET lease_until = :moment"
                " WHERE tenant_id = :tenant_id AND call_attempt_id = :attempt_id"
            ).bindparams(moment=moment, tenant_id=tenant_id, attempt_id=attempt_id)
        )


async def test_b235_model_call_lease_and_window_decisions_use_pg_clock(
    runtime_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-235] 真实预占/续租/失约：期限判定按 PG（进程钟偏差不延长模型调用执行权）。"""
    factory = runtime_factory
    tenant_id = f"b235-model-{uuid.uuid4().hex[:10]}"
    credential_id = await _seed_credential(factory, tenant_id, max_inflight=4)
    service = ModelCapacityService(lambda: factory, lease_duration_sec=30)
    pg = await _pg_now(factory)
    attempt_id = uuid.uuid4()
    reservation = await service.reserve(
        tenant_id=tenant_id,
        lease_owner=INSTANCE_ID,
        request=ModelCapacityReservationRequest(
            call_attempt_id=attempt_id,
            model_id=uuid.uuid4(),
            deployment_id=uuid.uuid4(),
            credential_id=credential_id,
            credential_revision=1,
            key_resource_id=uuid.uuid4(),
            quota_group_id=None,
            owner=ProbeCallOwner(probe_id=uuid.uuid4()),
            workload="PROBE",
            deadline_at=pg + timedelta(seconds=600),
            reserved_tokens=100,
        ),
    )
    # 预占租约期限来自 PG 时钟（≈ now + 租期），不是进程钟
    assert timedelta(seconds=0) < (reservation.lease_until - pg) <= timedelta(seconds=30) + CLOCK_TOLERANCE
    # A) PG 租约有效 + 进程钟拨快 6 小时：续租成功且期限不越过绝对期限、不跟随进程钟
    with monkeypatch.context() as patch:
        _freeze_process_clock(patch, model_capacity_service, pg + SKEW_FAST)
        renewal = await service.renew(
            tenant_id=tenant_id,
            call_attempt_id=attempt_id,
            epoch=reservation.epoch,
            lease_owner=INSTANCE_ID,
            deadline_at=pg + timedelta(seconds=600),
        )
    assert renewal.lease_until <= pg + timedelta(seconds=600)
    assert renewal.lease_until < pg + SKEW_FAST
    # B) PG 租约已过期 + 进程钟拨慢 1 小时：显式 MODEL_CALL_LEASE_LOST，不静默延长
    await _expire_lease(factory, tenant_id, attempt_id, pg - timedelta(seconds=1))
    with monkeypatch.context() as patch:
        _freeze_process_clock(patch, model_capacity_service, pg - SKEW_SLOW)
        with pytest.raises(AppError) as raised:
            await service.renew(
                tenant_id=tenant_id,
                call_attempt_id=attempt_id,
                epoch=reservation.epoch,
                lease_owner=INSTANCE_ID,
                deadline_at=pg + timedelta(seconds=600),
            )
    assert raised.value.code == ErrorCode.MODEL_CALL_LEASE_LOST


# --------------------------------------------------------------------------------------
# 7. Worker 租约续租与结果期限判定按 PG 时钟
# --------------------------------------------------------------------------------------


class _StubExecutor:
    """协议替身：只产出固定的执行信封（被测边界是期限判定，不是 Skill 执行）。"""

    def __init__(self, envelope: Mapping[str, Any]) -> None:
        self._envelope = dict(envelope)

    async def execute(self, task: TaskExecution) -> dict[str, Any]:
        return dict(self._envelope)


async def _seed_task(
    factory: async_sessionmaker[AsyncSession],
    *,
    status: str = "QUEUED",
    lease_owner: str | None = None,
    lease_until: datetime | None = None,
    not_before: datetime,
    deadline_at: datetime,
) -> uuid.UUID:
    task_id = uuid.uuid4()
    async with factory() as session, session.begin():
        session.add(
            TaskExecution(
                id=task_id,
                tenant_id=f"b235-{uuid.uuid4().hex[:12]}",
                agent_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
                intent_key="clock_probe",
                skill_id=uuid.uuid4(),
                skill_artifact_id=uuid.uuid4(),
                trigger_type="IMMEDIATE",
                execution_mode="ASYNC",
                task_type="SKILL",
                status=status,
                input_json={"probe": True},
                execution_snapshot_schema_version=1,
                execution_snapshot_json={"schema_version": 1},
                snapshot_hash=SNAPSHOT_HASH,
                idempotency_key=f"b235-{uuid.uuid4()}",
                priority=100,
                attempt=0,
                max_attempts=3,
                not_before=not_before,
                deadline_at=deadline_at,
                lease_owner=lease_owner,
                lease_until=lease_until,
                delivery_mode="FINAL_ONLY",
                delivery_status="PENDING",
                delivery_key=f"task:{task_id}:final",
                delivery_attempts=0,
            )
        )
    return task_id


async def _set_task_lease(
    factory: async_sessionmaker[AsyncSession],
    task_id: uuid.UUID,
    *,
    lease_owner: str | None,
    lease_until: datetime | None,
    deadline_at: datetime | None = None,
) -> None:
    async with factory() as session, session.begin():
        row = await session.get(TaskExecution, task_id)
        assert row is not None
        row.status = "RUNNING"
        row.lease_owner, row.lease_until = lease_owner, lease_until
        if deadline_at is not None:
            row.deadline_at = deadline_at


@dataclass(frozen=True, slots=True)
class _TaskState:
    status: str
    lease_owner: str | None
    lease_until: datetime | None
    error_code: str | None
    finished_at: datetime | None


async def _task_state(factory: async_sessionmaker[AsyncSession], task_id: uuid.UUID) -> _TaskState:
    async with factory() as session:
        row = await session.get(TaskExecution, task_id)
        assert row is not None, f"task {task_id} must exist"
        return _TaskState(
            str(row.status), row.lease_owner, row.lease_until, row.error_code, row.finished_at
        )


def _worker_settings(**overrides: Any) -> SharedSettings:
    values: dict[str, Any] = {
        "worker_execution_concurrency": 2,
        "task_heartbeat_sec": 1,
        "task_cancel_check_sec": 1,
        "task_lease_sec": 30,
    }
    values.update(overrides)
    return SharedSettings(**values)


async def test_b235_worker_lease_renewal_and_deadline_judgment_follow_pg_clock(
    worker_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-235] 续租失约显式停手；结果产出时按 PG 判断绝对期限，超期不写 COMPLETED。"""
    factory = worker_factory
    settings = _worker_settings()
    loop = WorkerLoop(factory, settings, instance_id=INSTANCE_ID)
    pg = await _pg_now(factory)
    # A) PG 租约有效 + 进程钟拨快 6 小时：续租成功，期限 = PG now + task_lease_sec
    task_a = await _seed_task(
        factory,
        status="RUNNING",
        lease_owner=INSTANCE_ID,
        lease_until=pg + timedelta(seconds=300),
        not_before=pg - timedelta(seconds=60),
        deadline_at=pg + timedelta(seconds=600),
    )
    with monkeypatch.context() as patch:
        _freeze_process_clock(patch, worker_service, pg + SKEW_FAST)
        owned, requested = await loop._renew_lease(task_a)
    assert owned is True and requested is False
    renewed = await _task_state(factory, task_a)
    assert renewed.lease_until is not None
    assert renewed.lease_until <= pg + timedelta(seconds=30) + CLOCK_TOLERANCE
    assert renewed.lease_until < pg + SKEW_FAST, "进程钟偏差不得延长执行权"
    # B) PG 租约已过期 + 进程钟拨慢 1 小时：显式失约（不重试、不延长）
    await _set_task_lease(
        factory, task_a, lease_owner=INSTANCE_ID, lease_until=pg - timedelta(seconds=1)
    )
    with monkeypatch.context() as patch:
        _freeze_process_clock(patch, worker_service, pg - SKEW_SLOW)
        owned, _requested = await loop._renew_lease(task_a)
    assert owned is False
    # C) PG 期限未过 + 进程钟拨快：结果照常写 COMPLETED（不误判超期）
    stub = _StubExecutor({"status": "SUCCEEDED", "result": {"text": "ok"}})
    task_c = await _seed_task(
        factory,
        status="RUNNING",
        lease_owner=INSTANCE_ID,
        lease_until=pg + timedelta(seconds=300),
        not_before=pg - timedelta(seconds=60),
        deadline_at=pg + timedelta(seconds=600),
    )
    task_c_row = await _task_row(factory, task_c)
    with monkeypatch.context() as patch:
        _freeze_process_clock(patch, worker_service, pg + SKEW_FAST)
        await WorkerLoop(
            factory, settings, instance_id=INSTANCE_ID, executor=stub
        )._supervise_task(task_c_row, now=None)
    assert (await _task_state(factory, task_c)).status == "COMPLETED"
    # D) PG 期限已过 + 进程钟拨慢：不写 COMPLETED，显式 FAILED/TASK_DEADLINE_EXCEEDED
    task_d = await _seed_task(
        factory,
        status="RUNNING",
        lease_owner=INSTANCE_ID,
        lease_until=pg + timedelta(seconds=300),
        not_before=pg - timedelta(seconds=60),
        deadline_at=pg - timedelta(seconds=1),
    )
    task_d_row = await _task_row(factory, task_d)
    with monkeypatch.context() as patch:
        _freeze_process_clock(patch, worker_service, pg - SKEW_SLOW)
        await WorkerLoop(
            factory, settings, instance_id=INSTANCE_ID, executor=stub
        )._supervise_task(task_d_row, now=None)
    exceeded = await _task_state(factory, task_d)
    assert exceeded.status == "FAILED" and exceeded.error_code == TASK_DEADLINE_EXCEEDED
    assert exceeded.finished_at is not None and abs(exceeded.finished_at - pg) <= CLOCK_TOLERANCE


async def _task_row(factory: async_sessionmaker[AsyncSession], task_id: uuid.UUID) -> TaskExecution:
    async with factory() as session:
        row = await session.get(TaskExecution, task_id)
        assert row is not None
        return row


# --------------------------------------------------------------------------------------
# 8. Task 领取与 deadline sweep 按 PG 时钟
# --------------------------------------------------------------------------------------


async def test_b235_task_claim_follows_pg_clock(
    worker_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-235] PG 未过期（进程钟拨快）照常领取；PG 已过期（进程钟拨慢）拒绝执行。"""
    factory = worker_factory
    claimer = TaskClaimer(factory, _worker_settings())
    pg = await _pg_now(factory)
    task_a = await _seed_task(
        factory, not_before=pg - timedelta(seconds=60), deadline_at=pg + timedelta(seconds=600)
    )
    with monkeypatch.context() as patch:
        _freeze_process_clock(patch, claimer_module, pg + SKEW_FAST)
        claimed = await claimer.claim_one(INSTANCE_ID)
    assert claimed is not None and claimed.id == task_a
    assert claimed.lease_until is not None
    assert claimed.lease_until <= pg + timedelta(seconds=30) + CLOCK_TOLERANCE
    assert claimed.lease_until < pg + SKEW_FAST, "租约期限不得跟随进程钟"
    task_b = await _seed_task(
        factory, not_before=pg - timedelta(seconds=120), deadline_at=pg - timedelta(seconds=1)
    )
    with monkeypatch.context() as patch:
        _freeze_process_clock(patch, claimer_module, pg - SKEW_SLOW)
        refused = await claimer.claim_one(INSTANCE_ID)
    assert refused is None
    assert (await _task_state(factory, task_b)).status == "QUEUED"


async def test_b235_task_deadline_sweep_follows_pg_clock(
    worker_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-235] 进程钟拨快不误杀未过期 Task；拨慢也照常按 PG 期限收尾。"""
    factory = worker_factory
    sweeper = DeadlineSweeper(factory, _worker_settings())
    pg = await _pg_now(factory)
    task = await _seed_task(
        factory, not_before=pg - timedelta(seconds=60), deadline_at=pg + timedelta(seconds=600)
    )
    with monkeypatch.context() as patch:
        _freeze_process_clock(patch, scheduler_service, pg + SKEW_FAST)
        assert await sweeper.sweep() == 0
    assert (await _task_state(factory, task)).status == "QUEUED"
    await _set_task_deadline(factory, task, pg - timedelta(seconds=1))
    with monkeypatch.context() as patch:
        _freeze_process_clock(patch, scheduler_service, pg - SKEW_SLOW)
        assert await sweeper.sweep() == 1
    swept = await _task_state(factory, task)
    assert swept.status == "FAILED" and swept.error_code == TASK_DEADLINE_EXCEEDED
    assert swept.finished_at is not None and abs(swept.finished_at - pg) <= CLOCK_TOLERANCE


async def _set_task_deadline(
    factory: async_sessionmaker[AsyncSession], task_id: uuid.UUID, deadline_at: datetime
) -> None:
    async with factory() as session, session.begin():
        row = await session.get(TaskExecution, task_id)
        assert row is not None
        row.deadline_at = deadline_at


# --------------------------------------------------------------------------------------
# 9. Schedule 触发的 Task 绝对期限冻结按 PG 时钟
# --------------------------------------------------------------------------------------


class _StaticResolver:
    """协议替身：按固定有效定义解析（被测边界是期限冻结，不是定义解析）。"""

    def __init__(self, response: ResolveDefinitionResponse) -> None:
        self._response = response

    async def resolve(
        self, agent_id: uuid.UUID, actor_user_id: uuid.UUID, tenant_id: str
    ) -> ResolveDefinitionResponse:
        return self._response


async def test_b235_task_deadline_freeze_uses_pg_clock(
    worker_factory: async_sessionmaker[AsyncSession],
    resolved: ResolveDefinitionResponse,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[B-235] 进程钟拨快 6 小时：新 Task 的 `not_before`/`deadline_at` 仍按 PG 冻结。"""
    factory = worker_factory
    settings = _worker_settings()
    pg = await _pg_now(factory)
    tenant_id = f"b235-schedule-{uuid.uuid4().hex[:10]}"
    skill = resolved.skills[0]
    async with factory() as session, session.begin():
        await ScheduleService(session, settings).create_schedule(
            tenant_id,
            CreateScheduleRequest(
                name="b235 clock probe",
                agent_id=resolved.agent.id,
                actor_user_id=uuid.uuid4(),
                intent_key="clock_probe",
                skill_id=skill.skill_id,
                input_template={},
                schedule=ScheduleSpec(type="ONCE", run_at=pg - timedelta(seconds=5), timezone="UTC"),
                delivery_route=DeliveryRouteInput(
                    channel="WECOM", bot_id="bot-b235", external_user_id="user-b235"
                ),
            ),
        )
    loop = SchedulerLoop(
        factory,
        _StaticResolver(resolved),
        settings,
        settings_client=NullPlatformSettingsClient(),
    )
    with monkeypatch.context() as patch:
        _freeze_process_clock(patch, scheduler_service, pg + SKEW_FAST)
        fired = await loop.run_once()
    assert fired is not None
    task = await _task_row(factory, fired)
    deadline_hours = default_platform_settings().task.default_deadline_hours
    assert task.deadline_at - task.not_before == timedelta(hours=deadline_hours)
    assert abs(task.not_before - pg) <= CLOCK_TOLERANCE
    assert task.not_before < pg + SKEW_FAST, "期限冻结不得跟随进程钟"
