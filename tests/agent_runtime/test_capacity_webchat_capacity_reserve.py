"""B-221 / B-01：真实 PG 上的模型容量原子预占、固定锁序、并发释放与失约停手。

真实边界（不 mock 存储或租约）：真实 `ModelCapacityService`/`ModelCapacityStore`、真实
PostgreSQL 容量行（`runtime.model_capacity`）与租约行（`runtime.model_call_lease`），
额度上限读真实 `control.model_credential`/`control.model_quota_group`。

- B-221（design §3.2/§3.3/§3.7）：池→quota_group→key_resource→attempt 固定锁序；重复
  attempt 不重复占位；跨模型/主调用/摘要/提炼/探针合并同一物理 Key 用量；锁内不联网。
- B-01（design §2.5）：并发预占/释放、容量边界、重复释放、异常退出/取消/租约失效不超发，
  失约执行停手。

锁序证据：① 以真实 `pg_locks` 观察「池 advisory 已持有但仍阻塞在行锁」；② 以真实存储的
调用记录断言组行先于 key 行、attempt 最后。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import socket
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.model_capacity_service import (
    CapacityRelease,
    CapacityReservation,
    ModelCapacityService,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.model_capacity_store import (
    RESOURCE_KEY,
    RESOURCE_QUOTA_GROUP,
    CapacitySnapshot,
    LeaseSnapshot,
    ModelCapacityStore,
    pool_lock_key,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_contracts.model_pool import (
    MemoryJobCallOwner,
    ModelCapacityReservationRequest,
    ProbeCallOwner,
    RunCallOwner,
)
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    AsyncTransaction,
    create_async_engine,
)

pytestmark = pytest.mark.integration

#: 锁内只允许连数据库：出现别端口的真实出站连接即违规
DATABASE_PORTS = frozenset({5432})


@dataclass(frozen=True, slots=True)
class Tenant:
    tenant_id: str


@dataclass(frozen=True, slots=True)
class Seed:
    """一把真实 Key（含可选共享额度组）的冻结身份。"""

    tenant_id: str
    credential_id: uuid.UUID
    key_resource_id: uuid.UUID
    quota_group_id: uuid.UUID | None = None


@dataclass(slots=True)
class RunCall:
    """一次调用尝试的冻结身份（真实 attempt/deployment/model）。"""

    model_id: uuid.UUID = field(default_factory=uuid.uuid4)
    deployment_id: uuid.UUID = field(default_factory=uuid.uuid4)
    attempt_id: uuid.UUID = field(default_factory=uuid.uuid4)


@pytest.fixture
async def tenant() -> AsyncIterator[Tenant]:
    """每用例独立租户；结束只删本租户行，不动共享库其它批次数据。"""
    context = Tenant(tenant_id=f"test-{uuid.uuid4().hex[:20]}")
    try:
        yield context
    finally:
        factory = get_session_factory()
        async with factory() as session:
            for statement in (
                "DELETE FROM runtime.model_call_lease WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.model_capacity WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.memory_consolidation_job WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.run_record WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.conversation WHERE tenant_id = :tenant_id",
                "DELETE FROM control.model_credential WHERE tenant_id = :tenant_id",
                "DELETE FROM control.model_quota_group WHERE tenant_id = :tenant_id",
            ):
                await session.execute(sa.text(statement), {"tenant_id": context.tenant_id})
            await session.commit()


@pytest.fixture
def service() -> ModelCapacityService:
    return ModelCapacityService(get_session_factory)


# --------------------------------------------------------------------------------------
# 真实控制面配置与运行时行
# --------------------------------------------------------------------------------------


async def _seed_quota_group(
    tenant_id: str, *, max_inflight: int, rpm: int | None = None, tpm: int | None = None
) -> uuid.UUID:
    group_id = uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO control.model_quota_group (id, tenant_id, key, rpm, tpm, max_inflight, revision)"
                " VALUES (:id, :tenant_id, :key, :rpm, :tpm, :max_inflight, 1)"
            ).bindparams(
                id=group_id,
                tenant_id=tenant_id,
                key=f"group-{uuid.uuid4().hex[:12]}",
                rpm=rpm,
                tpm=tpm,
                max_inflight=max_inflight,
            )
        )
        await session.commit()
    return group_id


async def _seed_credential(
    tenant_id: str,
    *,
    max_inflight: int,
    quota_group_id: uuid.UUID | None = None,
    enabled: bool = True,
) -> uuid.UUID:
    credential_id = uuid.uuid4()
    digest = hashlib.sha256(uuid.uuid4().hex.encode()).hexdigest()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO control.model_credential (id, tenant_id, key, provider_key, base_url, api_key,"
                " api_key_digest, quota_group_id, max_inflight, enabled, revision)"
                " VALUES (:id, :tenant_id, :key, 'openai', 'https://api.example.com/v1', :api_key,"
                " :digest, :quota_group_id, :max_inflight, :enabled, 1)"
            ).bindparams(
                id=credential_id,
                tenant_id=tenant_id,
                key=f"key-{uuid.uuid4().hex[:12]}",
                api_key=f"sk-test-{uuid.uuid4().hex}",
                digest=digest,
                quota_group_id=quota_group_id,
                max_inflight=max_inflight,
                enabled=enabled,
            )
        )
        await session.commit()
    return credential_id


async def _seed_share(
    tenant_id: str,
    *,
    key_max_inflight: int,
    group_max_inflight: int,
    rpm: int | None = None,
    tpm: int | None = None,
) -> Seed:
    """一把挂在共享额度组下的真实 Key（最常见形态）。"""
    group_id = await _seed_quota_group(
        tenant_id, max_inflight=group_max_inflight, rpm=rpm, tpm=tpm
    )
    return Seed(
        tenant_id=tenant_id,
        credential_id=await _seed_credential(
            tenant_id, max_inflight=key_max_inflight, quota_group_id=group_id
        ),
        key_resource_id=uuid.uuid4(),
        quota_group_id=group_id,
    )


async def _seed_run(tenant_id: str) -> uuid.UUID:
    conversation_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO runtime.conversation (id, tenant_id, user_id, agent_id, status, last_seq)"
                " VALUES (:id, :tenant_id, :user_id, :agent_id, 'ACTIVE', 0)"
            ).bindparams(
                id=conversation_id, tenant_id=tenant_id, user_id=uuid.uuid4(), agent_id=uuid.uuid4()
            )
        )
        await session.execute(
            sa.text(
                "INSERT INTO runtime.run_record (id, tenant_id, conversation_id, user_id, agent_id, status,"
                " input_text, trace_id, cancel_requested, lease_until, lease_owner)"
                " VALUES (:id, :tenant_id, :conversation_id, :user_id, :agent_id, 'RUNNING', 'x',"
                " :trace_id, false, :lease_until, 'rt-1')"
            ).bindparams(
                id=run_id,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                trace_id=uuid.uuid4().hex,
                lease_until=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
        await session.commit()
    return run_id


async def _seed_memory_job(tenant_id: str) -> uuid.UUID:
    job_id = uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO runtime.memory_consolidation_job (id, tenant_id, run_id, snapshot_id,"
                " conversation_id, user_id, through_seq, enqueued_scope_revision)"
                " VALUES (:id, :tenant_id, :run_id, :snapshot_id, :conversation_id, :user_id, 0, 1)"
            ).bindparams(
                id=job_id,
                tenant_id=tenant_id,
                run_id=uuid.uuid4(),
                snapshot_id=uuid.uuid4(),
                conversation_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
            )
        )
        await session.commit()
    return job_id


async def _reserve(
    service: ModelCapacityService,
    tenant: Tenant,
    seed: Seed,
    *,
    owner: Any,
    reserved_tokens: int,
    call: RunCall | None = None,
    workload: str = "INTERACTIVE",
    deadline_in_sec: int = 300,
    lease_owner: str = "rt-1",
) -> CapacityReservation:
    call = call or RunCall()
    reservation = await service.reserve(
        tenant_id=tenant.tenant_id,
        lease_owner=lease_owner,
        request=ModelCapacityReservationRequest(
            call_attempt_id=call.attempt_id,
            model_id=call.model_id,
            deployment_id=call.deployment_id,
            credential_id=seed.credential_id,
            credential_revision=1,
            key_resource_id=seed.key_resource_id,
            quota_group_id=seed.quota_group_id,
            owner=owner,
            workload=workload,
            deadline_at=datetime.now(UTC) + timedelta(seconds=deadline_in_sec),
            reserved_tokens=reserved_tokens,
        ),
    )
    return reservation


async def _reserve_or_code(
    service: ModelCapacityService, tenant: Tenant, seed: Seed, **kwargs: Any
) -> CapacityReservation | ErrorCode:
    try:
        return await _reserve(service, tenant, seed, **kwargs)
    except AppError as error:
        return ErrorCode(error.code)


def _probe() -> ProbeCallOwner:
    return ProbeCallOwner(probe_id=uuid.uuid4())


async def _capacity_row(tenant_id: str, resource_type: str, resource_id: uuid.UUID) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT inflight, reserved_requests, reserved_tokens, window_start"
                    " FROM runtime.model_capacity"
                    " WHERE tenant_id = :tenant_id AND resource_type = :resource_type"
                    " AND resource_id = :resource_id"
                ).bindparams(
                    tenant_id=tenant_id, resource_type=resource_type, resource_id=resource_id
                )
            )
        ).mappings().one()
    return dict(row)


async def _maybe_capacity_row(
    tenant_id: str, resource_type: str, resource_id: uuid.UUID
) -> dict[str, Any] | None:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT inflight FROM runtime.model_capacity"
                    " WHERE tenant_id = :tenant_id AND resource_type = :resource_type"
                    " AND resource_id = :resource_id"
                ).bindparams(
                    tenant_id=tenant_id, resource_type=resource_type, resource_id=resource_id
                )
            )
        ).mappings().one_or_none()
    return None if row is None else dict(row)


async def _lease_rows(tenant_id: str, *, attempt_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    async with get_session_factory()() as session:
        statement = (
            "SELECT call_attempt_id, run_id, memory_job_id, probe_id, key_resource_id, quota_group_id,"
            " workload, lease_owner, epoch, lease_until, reserved_tokens, state"
            " FROM runtime.model_call_lease WHERE tenant_id = :tenant_id"
        )
        params: dict[str, Any] = {"tenant_id": tenant_id}
        if attempt_id is not None:
            statement += " AND call_attempt_id = :attempt_id"
            params["attempt_id"] = attempt_id
        rows = (await session.execute(sa.text(statement), params)).mappings().all()
    return [dict(row) for row in rows]


async def _shift_window(
    tenant_id: str, resource_type: str, resource_id: uuid.UUID, *, minutes_ago: int
) -> None:
    """把窗口起点回拨到测试需要的桶边界（真实行更新，不改实现内的时间判断）。"""
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.model_capacity"
                " SET window_start = date_trunc('minute', now()) - make_interval(mins => :minutes_ago),"
                " update_time = now()"
                " WHERE tenant_id = :tenant_id AND resource_type = :resource_type"
                " AND resource_id = :resource_id"
            ).bindparams(
                tenant_id=tenant_id,
                resource_type=resource_type,
                resource_id=resource_id,
                minutes_ago=minutes_ago,
            )
        )
        await session.commit()


async def _expire_lease(tenant_id: str, attempt_id: uuid.UUID) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.model_call_lease SET lease_until = now() - interval '1 second',"
                " update_time = now() WHERE tenant_id = :tenant_id AND call_attempt_id = :attempt_id"
            ).bindparams(tenant_id=tenant_id, attempt_id=attempt_id)
        )
        await session.commit()


# --------------------------------------------------------------------------------------
# B-221：固定锁序 / attempt 幂等 / Key 用量合并 / 锁内不联网
# --------------------------------------------------------------------------------------


class _RecordingStore(ModelCapacityStore):
    """真实存储行为不变（逐调用直通 super），只记录加锁调用顺序。"""

    def __init__(self, session: AsyncSession, order: list[str]) -> None:
        super().__init__(session)
        self._order = order

    async def lock_pool(self, *, tenant_id: str, model_id: uuid.UUID) -> None:
        self._order.append("pool")
        await super().lock_pool(tenant_id=tenant_id, model_id=model_id)

    async def lock_capacity(
        self, *, tenant_id: str, resource_type: str, resource_id: uuid.UUID, window_start: datetime
    ) -> CapacitySnapshot:
        self._order.append(f"capacity:{resource_type}")
        return await super().lock_capacity(
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            window_start=window_start,
        )

    async def lock_attempt(self, *, tenant_id: str, call_attempt_id: uuid.UUID) -> LeaseSnapshot | None:
        self._order.append("attempt")
        return await super().lock_attempt(tenant_id=tenant_id, call_attempt_id=call_attempt_id)


async def test_b221_locks_pool_then_group_then_key_then_attempt(tenant: Tenant) -> None:
    """[B-221] 含共享额度组的预占按池→组→Key→attempt 固定顺序加锁。"""
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=5, group_max_inflight=5)
    order: list[str] = []
    service = ModelCapacityService(
        get_session_factory, store_factory=lambda session: _RecordingStore(session, order)
    )
    reservation = await _reserve(service, tenant, seed, owner=_probe(), reserved_tokens=100)
    assert reservation.state == "ACTIVE" and reservation.reused is False
    assert order == ["pool", f"capacity:{RESOURCE_QUOTA_GROUP}", f"capacity:{RESOURCE_KEY}", "attempt"]


#: 等行锁在真实 `pg_locks`/`pg_stat_activity` 里的两种等待态：等持锁事务提交（transactionid）
#: 与等行级元组锁（tuple）。并行批次下两种都实际出现过，观测必须同时覆盖。
_ROW_LOCK_WAITS = ("transactionid", "tuple")


async def _wait_for_advisory_holder(engine: AsyncEngine, key: int, *, tries: int = 600) -> dict[str, Any]:
    """等到「池 advisory 已授予且事务仍阻塞在行锁」的真实后端，返回其观测行。

    共享 dev 库上并行批次可能让预占排队数十秒，因此轮询上限按 30 秒给足；
    只等待目标状态到达（`granted` advisory + `Lock`/`transactionid|tuple`），达到即返回。
    这里不降低任何断言强度——断言对象仍是「先取池锁、后等行锁」的真实锁序。
    """
    classid, objid = (key >> 32) & 0xFFFFFFFF, key & 0xFFFFFFFF
    observed: list[dict[str, Any]] = []
    for _ in range(tries):
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    sa.text(
                        "SELECT l.pid, a.wait_event_type, a.wait_event, a.query FROM pg_locks l"
                        " JOIN pg_stat_activity a ON a.pid = l.pid"
                        " WHERE l.locktype = 'advisory' AND l.granted"
                        " AND l.objid::bigint = :objid"
                        " AND l.classid::bigint = :classid"
                    ).bindparams(classid=classid, objid=objid)
                )
            ).mappings().all()
        observed = [dict(row) for row in rows]
        for item in observed:
            if item["wait_event_type"] == "Lock" and item["wait_event"] in _ROW_LOCK_WAITS:
                return item
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"超时未观察到「池 advisory 已授予且阻塞在行锁」（wait∈{_ROW_LOCK_WAITS}）：最后观测={observed}"
    )


async def test_b221_pool_advisory_lock_held_before_capacity_row_lock(tenant: Tenant) -> None:
    """[B-221] 组行被别的连接占住时：池 advisory 已持有、事务仍在等行锁（真实 pg_locks）。"""
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=5, group_max_inflight=5)
    service = ModelCapacityService(get_session_factory)
    call = RunCall()
    blocker_engine = create_async_engine(SharedSettings().require_database_url())
    blocker: AsyncConnection | None = None
    transaction: AsyncTransaction | None = None
    waiter: asyncio.Task[Any] | None = None
    try:
        await _reserve(service, tenant, seed, owner=_probe(), reserved_tokens=10, call=call)
        blocker = await blocker_engine.connect()
        transaction = await blocker.begin()
        await blocker.execute(
            sa.text(
                "SELECT 1 FROM runtime.model_capacity WHERE tenant_id = :tenant_id"
                " AND resource_type = 'QUOTA_GROUP' AND resource_id = :resource_id FOR UPDATE"
            ).bindparams(tenant_id=tenant.tenant_id, resource_id=seed.quota_group_id)
        )
        waiter = asyncio.create_task(
            _reserve(
                service,
                tenant,
                seed,
                owner=_probe(),
                reserved_tokens=10,
                call=RunCall(model_id=call.model_id),
                lease_owner="rt-2",
            )
        )
        holder = await _wait_for_advisory_holder(
            blocker_engine, pool_lock_key(tenant.tenant_id, call.model_id)
        )
        assert holder["wait_event_type"] == "Lock", holder
        assert holder["wait_event"] in _ROW_LOCK_WAITS, holder
        await transaction.rollback()
        second = await waiter
        assert second.state == "ACTIVE"
        await blocker.close()
    finally:
        # 任何失败路径都不得把组行锁留在 `idle in transaction`：先放行 waiter，再关连接。
        # 清理步骤各自吞掉次生异常，避免掩盖主失败。
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


def _install_outbound_guard(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """记录非数据库端口的真实出站连接；任何 HTTP 客户端调用都视为锁内联网。"""
    violations: list[str] = []
    original_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        if isinstance(address, tuple) and len(address) > 1 and int(address[1]) not in DATABASE_PORTS:
            violations.append(f"{address[0]}:{address[1]}")
        return original_connect(self, address)

    async def forbidden_send(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("容量预占/释放路径不得发起 HTTP 调用")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_send)
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden_send)
    return violations


async def test_b221_reserve_and_release_never_leave_the_database(
    tenant: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-221] 锁内不联网：预占/释放只连 PG，容量满时明确不可用而非绕路解析。"""
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=1, group_max_inflight=1)
    violations = _install_outbound_guard(monkeypatch)
    service = ModelCapacityService(get_session_factory)
    reservation = await _reserve(service, tenant, seed, owner=_probe(), reserved_tokens=10)
    denied = await _reserve_or_code(service, tenant, seed, owner=_probe(), reserved_tokens=10)
    assert denied == ErrorCode.MODEL_CAPACITY_UNAVAILABLE
    released = await service.release_unsent(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        epoch=reservation.epoch,
        lease_owner="rt-1",
    )
    assert released.released is True
    assert violations == []


async def test_b221_duplicate_attempt_reserves_exactly_once(tenant: Tenant) -> None:
    """[B-221] 同一 attempt 顺序+并发重复预占只占位一次，并复用同一 reservation。"""
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=5, group_max_inflight=5)
    service = ModelCapacityService(get_session_factory)
    call = RunCall()
    request_owner = _probe()
    first = await _reserve(service, tenant, seed, owner=request_owner, reserved_tokens=321, call=call)
    assert first.reused is False

    repeated = await asyncio.gather(
        *(
            _reserve(
                service, tenant, seed, owner=request_owner, reserved_tokens=321, call=call,
                lease_owner=f"rt-{index}",
            )
            for index in range(4)
        )
    )
    assert {item.reservation_id for item in repeated} == {first.reservation_id}
    assert [item.reused for item in repeated] == [True, True, True, True]

    key_row = await _capacity_row(tenant.tenant_id, RESOURCE_KEY, seed.key_resource_id)
    group_row = await _capacity_row(tenant.tenant_id, RESOURCE_QUOTA_GROUP, seed.quota_group_id)
    assert (key_row["inflight"], key_row["reserved_requests"], key_row["reserved_tokens"]) == (1, 1, 321)
    assert (group_row["inflight"], group_row["reserved_requests"], group_row["reserved_tokens"]) == (
        1,
        1,
        321,
    )
    assert len(await _lease_rows(tenant.tenant_id)) == 1


async def test_b221_same_key_usage_merges_across_models_workloads_and_owners(tenant: Tenant) -> None:
    """[B-221] 跨模型/主调用/摘要/提炼/探针共用一个物理 Key 身份与同一行容量。"""
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=3, group_max_inflight=10)
    service = ModelCapacityService(get_session_factory)
    run_id = await _seed_run(tenant.tenant_id)
    job_id = await _seed_memory_job(tenant.tenant_id)
    model_a, model_b = uuid.uuid4(), uuid.uuid4()

    granted = [
        await _reserve(
            service, tenant, seed, owner=RunCallOwner(run_id=run_id), reserved_tokens=100,
            call=RunCall(model_id=model_a),
        ),
        await _reserve(
            service, tenant, seed, owner=RunCallOwner(run_id=run_id), reserved_tokens=100,
            call=RunCall(model_id=model_a), workload="BACKGROUND",
        ),
        await _reserve(
            service, tenant, seed, owner=MemoryJobCallOwner(memory_job_id=job_id), reserved_tokens=100,
            call=RunCall(model_id=model_b), workload="BACKGROUND",
        ),
    ]
    assert all(item.state == "ACTIVE" for item in granted)

    denied = await _reserve_or_code(
        service, tenant, seed, owner=_probe(), reserved_tokens=1, workload="PROBE",
        call=RunCall(model_id=model_b),
    )
    assert denied == ErrorCode.MODEL_CAPACITY_UNAVAILABLE
    key_row = await _capacity_row(tenant.tenant_id, RESOURCE_KEY, seed.key_resource_id)
    assert (key_row["inflight"], key_row["reserved_requests"]) == (3, 3)

    leases = await _lease_rows(tenant.tenant_id)
    assert len(leases) == 3
    assert {row["key_resource_id"] for row in leases} == {seed.key_resource_id}
    assert sum(row["run_id"] is not None for row in leases) == 2
    assert sum(row["memory_job_id"] is not None for row in leases) == 1
    assert {row["workload"] for row in leases} == {"INTERACTIVE", "BACKGROUND"}


async def test_b221_quota_group_window_counts_tokens_shared_across_keys(tenant: Tenant) -> None:
    """[B-221] 额度组 TPM 是 60 秒保守窗口：组内多把 Key 的预留 token 合并计数。"""
    group_id = await _seed_quota_group(tenant.tenant_id, max_inflight=10, tpm=1000)
    seed_a = Seed(
        tenant.tenant_id,
        await _seed_credential(tenant.tenant_id, max_inflight=10, quota_group_id=group_id),
        uuid.uuid4(),
        group_id,
    )
    seed_b = Seed(
        tenant.tenant_id,
        await _seed_credential(tenant.tenant_id, max_inflight=10, quota_group_id=group_id),
        uuid.uuid4(),
        group_id,
    )
    service = ModelCapacityService(get_session_factory)
    for seed in (seed_a, seed_b):
        await _reserve(service, tenant, seed, owner=_probe(), reserved_tokens=400)

    group_row = await _capacity_row(tenant.tenant_id, RESOURCE_QUOTA_GROUP, group_id)
    assert (group_row["reserved_tokens"], group_row["reserved_requests"]) == (800, 2)
    denied = await _reserve_or_code(service, tenant, seed_a, owner=_probe(), reserved_tokens=201)
    assert denied == ErrorCode.MODEL_CAPACITY_UNAVAILABLE
    assert (await _capacity_row(tenant.tenant_id, RESOURCE_QUOTA_GROUP, group_id))["reserved_tokens"] == 800


async def test_b221_window_keeps_boundary_bucket_until_it_leaves_the_window(tenant: Tenant) -> None:
    """[B-221] 保守滚动 60 秒：相交的边界桶不清空，完全过期才清零。"""
    seed = await _seed_share(
        tenant.tenant_id, key_max_inflight=10, group_max_inflight=10, rpm=2
    )
    service = ModelCapacityService(get_session_factory)
    for _ in range(2):
        await _reserve(service, tenant, seed, owner=_probe(), reserved_tokens=5)

    await _shift_window(tenant.tenant_id, RESOURCE_QUOTA_GROUP, seed.quota_group_id, minutes_ago=1)
    boundary = await _reserve_or_code(service, tenant, seed, owner=_probe(), reserved_tokens=5)
    assert boundary == ErrorCode.MODEL_CAPACITY_UNAVAILABLE
    retained = await _capacity_row(tenant.tenant_id, RESOURCE_QUOTA_GROUP, seed.quota_group_id)
    assert retained["reserved_requests"] == 2

    await _shift_window(tenant.tenant_id, RESOURCE_QUOTA_GROUP, seed.quota_group_id, minutes_ago=2)
    assert await _reserve(service, tenant, seed, owner=_probe(), reserved_tokens=5)
    reset = await _capacity_row(tenant.tenant_id, RESOURCE_QUOTA_GROUP, seed.quota_group_id)
    assert reset["reserved_requests"] == 1


async def test_b221_unknown_or_disabled_credential_is_rejected(tenant: Tenant) -> None:
    """[B-221] 冻结引用缺失/停用不得占位：归属缺失 404，停用 409。"""
    service = ModelCapacityService(get_session_factory)
    missing = Seed(tenant.tenant_id, uuid.uuid4(), uuid.uuid4())
    assert await _reserve_or_code(service, tenant, missing, owner=_probe(), reserved_tokens=10) == (
        ErrorCode.COMMON_NOT_FOUND
    )
    disabled = Seed(
        tenant.tenant_id,
        await _seed_credential(tenant.tenant_id, max_inflight=5, enabled=False),
        uuid.uuid4(),
    )
    assert await _reserve_or_code(service, tenant, disabled, owner=_probe(), reserved_tokens=10) == (
        ErrorCode.MODEL_DISABLED
    )
    assert await _lease_rows(tenant.tenant_id) == []


async def test_b221_expired_deadline_never_reserves(tenant: Tenant) -> None:
    """[B-221] 绝对期限已过：返回等待超时，不写租约、不建容量行。"""
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=3, group_max_inflight=3)
    service = ModelCapacityService(get_session_factory)
    timed_out = await _reserve_or_code(
        service, tenant, seed, owner=_probe(), reserved_tokens=10, deadline_in_sec=-5
    )
    assert timed_out == ErrorCode.MODEL_WAIT_TIMEOUT
    assert await _lease_rows(tenant.tenant_id) == []
    assert await _maybe_capacity_row(tenant.tenant_id, RESOURCE_KEY, seed.key_resource_id) is None


# --------------------------------------------------------------------------------------
# B-01：并发边界、重复释放、异常退出/取消/租约失效不超发、失约停手
# --------------------------------------------------------------------------------------


async def test_b01_concurrent_reserves_stop_at_key_capacity(tenant: Tenant) -> None:
    """[B-01] 并发预占严格收敛到上限：成功数=min(key, group) 上限，其余明确不可用。"""
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=2, group_max_inflight=5)
    service = ModelCapacityService(get_session_factory)
    results = await asyncio.gather(
        *(
            _reserve_or_code(
                service, tenant, seed, owner=_probe(), reserved_tokens=10, lease_owner=f"rt-{index}"
            )
            for index in range(8)
        ),
        return_exceptions=True,
    )
    assert not [result for result in results if isinstance(result, BaseException)], results
    granted = [result for result in results if isinstance(result, CapacityReservation)]
    assert len(granted) == 2
    assert [result for result in results if isinstance(result, ErrorCode)] == [
        ErrorCode.MODEL_CAPACITY_UNAVAILABLE
    ] * 6
    key_row = await _capacity_row(tenant.tenant_id, RESOURCE_KEY, seed.key_resource_id)
    group_row = await _capacity_row(tenant.tenant_id, RESOURCE_QUOTA_GROUP, seed.quota_group_id)
    assert key_row["inflight"] == 2 and group_row["inflight"] == 2
    assert len(await _lease_rows(tenant.tenant_id)) == 2


async def test_b01_mixed_pools_concurrently_stay_ordered_without_oversubscription(tenant: Tenant) -> None:
    """[B-01] 跨模型/Key 共享同一额度组并发：先后关系一致（无死锁），总量不超发。"""
    group_id = await _seed_quota_group(tenant.tenant_id, max_inflight=3)
    seeds = [
        Seed(
            tenant.tenant_id,
            await _seed_credential(tenant.tenant_id, max_inflight=2, quota_group_id=group_id),
            uuid.uuid4(),
            group_id,
        )
        for _ in range(2)
    ]
    service = ModelCapacityService(get_session_factory)
    results = await asyncio.gather(
        *(
            _reserve_or_code(
                service, tenant, seeds[index % 2], owner=_probe(), reserved_tokens=10,
                lease_owner=f"rt-{index}",
            )
            for index in range(8)
        ),
        return_exceptions=True,
    )
    assert not [result for result in results if isinstance(result, BaseException)], results
    assert len([result for result in results if isinstance(result, CapacityReservation)]) == 3
    assert {result for result in results if isinstance(result, ErrorCode)} == {
        ErrorCode.MODEL_CAPACITY_UNAVAILABLE
    }
    group_row = await _capacity_row(tenant.tenant_id, RESOURCE_QUOTA_GROUP, group_id)
    assert group_row["inflight"] == 3
    key_total = 0
    for seed in seeds:
        key_total += (await _capacity_row(tenant.tenant_id, RESOURCE_KEY, seed.key_resource_id))["inflight"]
    assert key_total == 3


async def test_b01_release_frees_capacity_once_and_duplicate_release_is_noop(tenant: Tenant) -> None:
    """[B-01] 释放只减一次：重复释放不再减容量，计数不出现负数。"""
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=2, group_max_inflight=2)
    service = ModelCapacityService(get_session_factory)
    reservation = await _reserve(service, tenant, seed, owner=_probe(), reserved_tokens=150)
    released = await service.release_unsent(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        epoch=reservation.epoch,
        lease_owner="rt-1",
    )
    assert released == CapacityRelease(
        reservation_id=reservation.reservation_id,
        call_attempt_id=reservation.call_attempt_id,
        epoch=reservation.epoch,
        state="SETTLED",
        released=True,
    )
    duplicate = await service.release_unsent(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        epoch=reservation.epoch,
        lease_owner="rt-1",
    )
    assert duplicate.released is False and duplicate.state == "SETTLED"
    for resource_type, resource_id in (
        (RESOURCE_KEY, seed.key_resource_id),
        (RESOURCE_QUOTA_GROUP, seed.quota_group_id),
    ):
        row = await _capacity_row(tenant.tenant_id, resource_type, resource_id)
        assert (row["inflight"], row["reserved_requests"], row["reserved_tokens"]) == (0, 0, 0)
    assert (await _lease_rows(tenant.tenant_id))[0]["state"] == "SETTLED"


async def test_b01_expired_lease_keeps_capacity_and_fences_the_executor(tenant: Tenant) -> None:
    """[B-01] 租约失效不超发：过期不等于远端已停，占用保留且执行方被驳回。"""
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=1, group_max_inflight=1)
    service = ModelCapacityService(get_session_factory)
    reservation = await _reserve(service, tenant, seed, owner=_probe(), reserved_tokens=10)
    await service.ensure_lease_held(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        epoch=reservation.epoch,
        lease_owner="rt-1",
    )
    await _expire_lease(tenant.tenant_id, reservation.call_attempt_id)
    with pytest.raises(AppError) as fenced:
        await service.ensure_lease_held(
            tenant_id=tenant.tenant_id,
            call_attempt_id=reservation.call_attempt_id,
            epoch=reservation.epoch,
            lease_owner="rt-1",
        )
    assert fenced.value.code == ErrorCode.MODEL_CALL_LEASE_LOST
    with pytest.raises(AppError) as wrong_owner:
        await service.ensure_lease_held(
            tenant_id=tenant.tenant_id,
            call_attempt_id=reservation.call_attempt_id,
            epoch=reservation.epoch,
            lease_owner="rt-other",
        )
    assert wrong_owner.value.code == ErrorCode.MODEL_CALL_LEASE_LOST

    blocked = await _reserve_or_code(service, tenant, seed, owner=_probe(), reserved_tokens=10)
    assert blocked == ErrorCode.MODEL_CAPACITY_UNAVAILABLE
    key_row = await _capacity_row(tenant.tenant_id, RESOURCE_KEY, seed.key_resource_id)
    assert (key_row["inflight"], key_row["reserved_requests"]) == (1, 1)


async def test_b01_cancelled_or_crashed_call_retains_capacity_as_unknown(tenant: Tenant) -> None:
    """[B-01] 取消/异常退出只能声明 UNKNOWN：占用保留，且不能按未发送释放。"""
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=1, group_max_inflight=1)
    service = ModelCapacityService(get_session_factory)
    reservation = await _reserve(
        service, tenant, seed, owner=RunCallOwner(run_id=await _seed_run(tenant.tenant_id)),
        reserved_tokens=77,
    )
    unknown = await service.mark_unknown(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        epoch=reservation.epoch,
        lease_owner="rt-1",
    )
    assert unknown.state == "UNKNOWN" and unknown.released is False
    key_row = await _capacity_row(tenant.tenant_id, RESOURCE_KEY, seed.key_resource_id)
    assert (key_row["inflight"], key_row["reserved_tokens"]) == (1, 77)

    with pytest.raises(AppError) as unsafe:
        await service.release_unsent(
            tenant_id=tenant.tenant_id,
            call_attempt_id=reservation.call_attempt_id,
            epoch=reservation.epoch,
            lease_owner="rt-1",
        )
    assert unsafe.value.code == ErrorCode.MODEL_CALL_OUTCOME_UNKNOWN
    assert (await _capacity_row(tenant.tenant_id, RESOURCE_KEY, seed.key_resource_id))["inflight"] == 1
    assert (await _lease_rows(tenant.tenant_id))[0]["state"] == "UNKNOWN"
