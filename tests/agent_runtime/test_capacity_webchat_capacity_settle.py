"""B-222：真实 PG 上的调用续租、usage 结算与 UNKNOWN 保留。

真实边界（不 mock 存储或租约）：真实 `ModelCapacityService`/`ModelCapacityStore`、真实
PostgreSQL 容量行（`runtime.model_capacity`）与租约行（`runtime.model_call_lease`），
额度上限读真实 `control.model_credential`/`control.model_quota_group`。

- 租约状态机 ACTIVE/UNKNOWN/SETTLED：SETTLED 幂等（重复结算/释放不再二次扣减）；UNKNOWN
  保留占用（不释放、不超发），只有完成证据（或确定未发送）才结算释放；
- 续租核 epoch/owner 且要求当前租约仍有效（过期即失约停手）；续租只前进不倒退，且不超过
  调用冻结的绝对期限；
- 本地超时（本地租约过期）不等于远端已停：`ACTIVE→UNKNOWN` 清扫只保留占用，等证据才结算；
- 成功按实际 usage 结算并释放多余预占；失败不假定供应商未计费（无 usage 保留保守预占）；
- 跨分钟结算只改仍计入当窗的租约，窗口已滚过时不污染新桶、不重复扣减。
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.model_capacity_service import (
    CapacityRelease,
    CapacityRenewal,
    CapacityReservation,
    ModelCapacityService,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.model_capacity_store import (
    RESOURCE_KEY,
    RESOURCE_QUOTA_GROUP,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_contracts.model_pool import (
    ModelCallSettlement,
    ModelCapacityReservationRequest,
    ModelTokenUsage,
    ProbeCallOwner,
    RemoteCompletionProof,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

pytestmark = pytest.mark.integration


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
# 真实控制面配置、运行时行与观察工具
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


def _deadline(seconds: int) -> datetime:
    """调用冻结的绝对期限（真实时钟，测试自己持有以便续租核同一份）。"""
    return datetime.now(UTC) + timedelta(seconds=seconds)


async def _reserve(
    service: ModelCapacityService,
    tenant: Tenant,
    seed: Seed,
    *,
    owner: Any,
    reserved_tokens: int,
    deadline_at: datetime,
    call: RunCall | None = None,
    workload: str = "INTERACTIVE",
    lease_owner: str = "rt-1",
) -> CapacityReservation:
    call = call or RunCall()
    request = ModelCapacityReservationRequest(
        call_attempt_id=call.attempt_id,
        model_id=call.model_id,
        deployment_id=call.deployment_id,
        credential_id=seed.credential_id,
        credential_revision=1,
        key_resource_id=seed.key_resource_id,
        quota_group_id=seed.quota_group_id,
        owner=owner,
        workload=workload,
        deadline_at=deadline_at,
        reserved_tokens=reserved_tokens,
    )
    return await service.reserve(tenant_id=tenant.tenant_id, lease_owner=lease_owner, request=request)


async def _reserve_or_code(
    service: ModelCapacityService, tenant: Tenant, seed: Seed, **kwargs: Any
) -> CapacityReservation | ErrorCode:
    try:
        return await _reserve(service, tenant, seed, **kwargs)
    except AppError as error:
        return ErrorCode(error.code)


def _probe() -> ProbeCallOwner:
    return ProbeCallOwner(probe_id=uuid.uuid4())


def _settlement(
    reservation: CapacityReservation,
    outcome: str,
    *,
    usage: ModelTokenUsage | None = None,
    proof: RemoteCompletionProof | None = None,
    epoch: int | None = None,
    lease_owner: str = "rt-1",
) -> ModelCallSettlement:
    return ModelCallSettlement(
        epoch=reservation.epoch if epoch is None else epoch,
        lease_owner=lease_owner,
        outcome=outcome,
        usage=usage,
        remote_completion=proof,
    )


def _proof(kind: str = "PROVIDER_CONFIRMED", reference: str = "provider-response-a") -> RemoteCompletionProof:
    return RemoteCompletionProof(kind=kind, reference=reference, completed_at=datetime.now(UTC))


def _usage(input_tokens: int, output_tokens: int) -> ModelTokenUsage:
    return ModelTokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)


async def _renew_or_code(
    service: ModelCapacityService,
    tenant: Tenant,
    reservation: CapacityReservation,
    *,
    lease_owner: str,
    deadline_at: datetime,
) -> CapacityRenewal | ErrorCode:
    try:
        return await service.renew(
            tenant_id=tenant.tenant_id,
            call_attempt_id=reservation.call_attempt_id,
            epoch=reservation.epoch,
            lease_owner=lease_owner,
            deadline_at=deadline_at,
        )
    except AppError as error:
        return ErrorCode(error.code)


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


async def _lease_row(tenant_id: str, attempt_id: uuid.UUID) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT call_attempt_id, lease_owner, epoch, lease_until, reserved_tokens, state"
                    " FROM runtime.model_call_lease WHERE tenant_id = :tenant_id"
                    " AND call_attempt_id = :attempt_id"
                ).bindparams(tenant_id=tenant_id, attempt_id=attempt_id)
            )
        ).mappings().one()
    return dict(row)


async def _counts(
    tenant: Tenant, seed: Seed
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """(KEY 计数, 组计数)，均为 (inflight, reserved_requests, reserved_tokens)。"""
    key_row = await _capacity_row(tenant.tenant_id, RESOURCE_KEY, seed.key_resource_id)
    group_row = await _capacity_row(tenant.tenant_id, RESOURCE_QUOTA_GROUP, seed.quota_group_id)
    return (
        (key_row["inflight"], key_row["reserved_requests"], key_row["reserved_tokens"]),
        (group_row["inflight"], group_row["reserved_requests"], group_row["reserved_tokens"]),
    )


async def _expire_lease(tenant_id: str, attempt_id: uuid.UUID) -> None:
    """把真实 `lease_until` 推到过去（本地超时），不是伪造时钟。"""
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.model_call_lease SET lease_until = now() - interval '1 second',"
                " update_time = now() WHERE tenant_id = :tenant_id AND call_attempt_id = :attempt_id"
            ).bindparams(tenant_id=tenant_id, attempt_id=attempt_id)
        )
        await session.commit()


async def _shift_window(tenant_id: str, seed: Seed, *, minutes_ago: int) -> None:
    """把 KEY 与组行的窗口起点回拨到测试需要的桶边界（同一事务，两行取同一 PG 时间）。"""
    resources = [(RESOURCE_KEY, seed.key_resource_id)]
    if seed.quota_group_id is not None:
        resources.append((RESOURCE_QUOTA_GROUP, seed.quota_group_id))
    async with get_session_factory()() as session, session.begin():
        for resource_type, resource_id in resources:
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


async def _backdate_lease(tenant_id: str, attempt_id: uuid.UUID, *, minutes_ago: int) -> None:
    """把租约签发时间回拨（模拟调用发生在更早的窗口桶，跨分钟结算用）。"""
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.model_call_lease"
                " SET create_time = create_time - make_interval(mins => :minutes_ago), update_time = now()"
                " WHERE tenant_id = :tenant_id AND call_attempt_id = :attempt_id"
            ).bindparams(tenant_id=tenant_id, attempt_id=attempt_id, minutes_ago=minutes_ago)
        )
        await session.commit()


async def _capacity_row_count(tenant_id: str) -> int:
    async with get_session_factory()() as session:
        return int(
            (
                await session.execute(
                    sa.text(
                        "SELECT count(*) FROM runtime.model_capacity WHERE tenant_id = :tenant_id"
                    ).bindparams(tenant_id=tenant_id)
                )
            ).scalar_one()
        )


async def _wait_for_lease_row_waiters(
    engine: AsyncEngine, xid: str, *, count: int, tries: int = 600
) -> None:
    """在真实 `pg_locks`/`pg_stat_activity` 里等到租约行上有 count 个未授予的等待者。

    行锁等待在 PostgreSQL 上有两条路径：等持锁事务结束（`transactionid` 未授予锁）与等行上
    已存在的行锁（`tuple` 未授予锁，锁属于租约表）。两条都算真实等待。共享 dev 库上并行批次
    可能让行锁排队数十秒，因此轮询上限按 30 秒给足；这里只等待“观察到达”，不改变断言强度。
    """
    statement = sa.text(
        "SELECT count(DISTINCT a.pid) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid"
        " WHERE NOT l.granted AND a.wait_event_type = 'Lock' AND a.state = 'active'"
        " AND (l.transactionid::text = :xid OR l.relation = 'runtime.model_call_lease'::regclass)"
    )
    waiting = 0
    for _ in range(tries):
        async with engine.connect() as connection:
            waiting = (await connection.execute(statement, {"xid": xid})).scalar_one()
        if waiting >= count:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"租约行上的等待者未在超时内到达 {count} 个（观测到 {waiting}）")


async def _hold_attempt_row(
    tenant_id: str, attempt_id: uuid.UUID
) -> tuple[AsyncEngine, Any, Any, str]:
    """用独立连接真实占住 attempt 行锁，返回 (engine, connection, transaction, 本事务 xid)。"""
    engine = create_async_engine(SharedSettings().require_database_url())
    connection = await engine.connect()
    transaction = await connection.begin()
    await connection.execute(
        sa.text(
            "SELECT 1 FROM runtime.model_call_lease WHERE tenant_id = :tenant_id"
            " AND call_attempt_id = :attempt_id FOR UPDATE"
        ).bindparams(tenant_id=tenant_id, attempt_id=attempt_id)
    )
    xid = str(await connection.scalar(sa.text("SELECT pg_current_xact_id()::text")))
    return engine, connection, transaction, xid


# --------------------------------------------------------------------------------------
# 续租：epoch/owner CAS、只前进不倒退、失约即停手、期限约束
# --------------------------------------------------------------------------------------


async def test_b222_renew_extends_lease_until_deadline_and_keeps_the_lease(
    tenant: Tenant, service: ModelCapacityService
) -> None:
    """[B-222] 续租推进 `lease_until`（不越过冻结的绝对期限），不改变容量占用与身份。"""
    deadline = _deadline(300)
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=2, group_max_inflight=2)
    reservation = await _reserve(
        service, tenant, seed, owner=_probe(), reserved_tokens=100, deadline_at=deadline
    )
    renewal = await service.renew(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        epoch=reservation.epoch,
        lease_owner="rt-1",
        deadline_at=deadline,
    )
    assert renewal.reservation_id == reservation.reservation_id
    assert renewal.call_attempt_id == reservation.call_attempt_id
    assert renewal.epoch == reservation.epoch and renewal.lease_owner == "rt-1"
    assert renewal.state == "ACTIVE"
    assert reservation.lease_until < renewal.lease_until <= deadline
    assert (await _lease_row(tenant.tenant_id, reservation.call_attempt_id))["lease_until"] == (
        renewal.lease_until
    )
    # 续租后仍持有租约，且容量计数不被续租改动（不重复占位、不提前释放）
    await service.ensure_lease_held(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        epoch=renewal.epoch,
        lease_owner="rt-1",
    )
    assert await _counts(tenant, seed) == ((1, 1, 100), (1, 1, 100))


async def test_b222_renew_fences_stale_holders_and_finished_leases(
    tenant: Tenant, service: ModelCapacityService
) -> None:
    """[B-222] epoch/owner 不符即失约；SETTLED/UNKNOWN 上不得续租（执行方必须停手）。"""
    deadline = _deadline(300)
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=2, group_max_inflight=2)
    first = await _reserve(
        service, tenant, seed, owner=_probe(), reserved_tokens=100, deadline_at=deadline
    )
    for epoch, lease_owner in ((first.epoch + 1, "rt-1"), (first.epoch, "rt-other")):
        with pytest.raises(AppError) as fenced:
            await service.renew(
                tenant_id=tenant.tenant_id,
                call_attempt_id=first.call_attempt_id,
                epoch=epoch,
                lease_owner=lease_owner,
                deadline_at=deadline,
            )
        assert fenced.value.code == ErrorCode.MODEL_CALL_LEASE_LOST
    assert (await _lease_row(tenant.tenant_id, first.call_attempt_id))["lease_until"] == (
        first.lease_until
    )

    settled = await service.release_unsent(
        tenant_id=tenant.tenant_id,
        call_attempt_id=first.call_attempt_id,
        epoch=first.epoch,
        lease_owner="rt-1",
    )
    assert settled.state == "SETTLED"
    with pytest.raises(AppError) as settled_lost:
        await service.renew(
            tenant_id=tenant.tenant_id,
            call_attempt_id=first.call_attempt_id,
            epoch=first.epoch,
            lease_owner="rt-1",
            deadline_at=deadline,
        )
    assert settled_lost.value.code == ErrorCode.MODEL_CALL_LEASE_LOST

    call = RunCall()
    second = await _reserve(
        service, tenant, seed, owner=_probe(), reserved_tokens=100, deadline_at=deadline, call=call
    )
    await service.mark_unknown(
        tenant_id=tenant.tenant_id,
        call_attempt_id=second.call_attempt_id,
        epoch=second.epoch,
        lease_owner="rt-1",
    )
    with pytest.raises(AppError) as unknown_lost:
        await service.renew(
            tenant_id=tenant.tenant_id,
            call_attempt_id=second.call_attempt_id,
            epoch=second.epoch,
            lease_owner="rt-1",
            deadline_at=deadline,
        )
    assert unknown_lost.value.code == ErrorCode.MODEL_CALL_LEASE_LOST
    assert (await _lease_row(tenant.tenant_id, second.call_attempt_id))["state"] == "UNKNOWN"


async def test_b222_renew_refuses_expired_lease_and_keeps_capacity_reserved(
    tenant: Tenant, service: ModelCapacityService
) -> None:
    """[B-222] 本地超时即失约：不能靠一次续租把失效执行权续到未来，占用保留不超发。"""
    deadline = _deadline(600)
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=1, group_max_inflight=1)
    reservation = await _reserve(
        service, tenant, seed, owner=_probe(), reserved_tokens=90, deadline_at=deadline
    )
    await _expire_lease(tenant.tenant_id, reservation.call_attempt_id)
    expired_until = (await _lease_row(tenant.tenant_id, reservation.call_attempt_id))["lease_until"]

    with pytest.raises(AppError) as lost:
        await service.renew(
            tenant_id=tenant.tenant_id,
            call_attempt_id=reservation.call_attempt_id,
            epoch=reservation.epoch,
            lease_owner="rt-1",
            deadline_at=deadline,
        )
    assert lost.value.code == ErrorCode.MODEL_CALL_LEASE_LOST
    with pytest.raises(AppError) as fenced:
        await service.ensure_lease_held(
            tenant_id=tenant.tenant_id,
            call_attempt_id=reservation.call_attempt_id,
            epoch=reservation.epoch,
            lease_owner="rt-1",
        )
    assert fenced.value.code == ErrorCode.MODEL_CALL_LEASE_LOST
    assert (await _lease_row(tenant.tenant_id, reservation.call_attempt_id))["lease_until"] == (
        expired_until
    )

    blocked = await _reserve_or_code(
        service, tenant, seed, owner=_probe(), reserved_tokens=10, deadline_at=deadline
    )
    assert blocked == ErrorCode.MODEL_CAPACITY_UNAVAILABLE
    assert await _counts(tenant, seed) == ((1, 1, 90), (1, 1, 90))

    # 失约停手之后按证据结算（服务端硬超时），而不是假定供应商未计费
    settled = await service.settle(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        settlement=_settlement(
            reservation, "FAILED", proof=_proof(kind="SERVER_HARD_TIMEOUT", reference="server-hard-a")
        ),
    )
    assert settled.state == "SETTLED" and settled.released is True
    assert await _counts(tenant, seed) == ((0, 1, 90), (0, 1, 90))


async def test_b222_renew_refuses_a_deadline_that_already_passed(
    tenant: Tenant, service: ModelCapacityService
) -> None:
    """[B-222] 绝对期限已过不再续租（调用必须停手），期限不被延长。

    冻结的绝对期限随 Run/Task 保存在调用方，租约行只存 `lease_until`，因此这里由调用方
    给出已过的期限，钉住拒绝分支而不必等待真实时钟。
    """
    deadline = _deadline(300)
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=2, group_max_inflight=2)
    reservation = await _reserve(
        service, tenant, seed, owner=_probe(), reserved_tokens=100, deadline_at=deadline
    )
    with pytest.raises(AppError) as expired:
        await service.renew(
            tenant_id=tenant.tenant_id,
            call_attempt_id=reservation.call_attempt_id,
            epoch=reservation.epoch,
            lease_owner="rt-1",
            deadline_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    assert expired.value.code == ErrorCode.MODEL_WAIT_TIMEOUT
    assert (await _lease_row(tenant.tenant_id, reservation.call_attempt_id))["lease_until"] == (
        reservation.lease_until
    )


async def test_b222_concurrent_renewals_serialize_without_moving_the_lease_backwards(
    tenant: Tenant, service: ModelCapacityService
) -> None:
    """[B-222] 并发续租在真实行锁上串行：全部成功且期限单调前进，陈旧 owner 被驳回。"""
    deadline = _deadline(600)
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=2, group_max_inflight=2)
    reservation = await _reserve(
        service, tenant, seed, owner=_probe(), reserved_tokens=100, deadline_at=deadline
    )

    async def renew(lease_owner: str) -> CapacityRenewal:
        return await service.renew(
            tenant_id=tenant.tenant_id,
            call_attempt_id=reservation.call_attempt_id,
            epoch=reservation.epoch,
            lease_owner=lease_owner,
            deadline_at=deadline,
        )

    results = await asyncio.gather(
        *(renew("rt-1") for _ in range(3)),
        _renew_or_code(service, tenant, reservation, lease_owner="rt-other", deadline_at=deadline),
        return_exceptions=True,
    )
    assert not [item for item in results if isinstance(item, BaseException)], results
    granted = [item for item in results if isinstance(item, CapacityRenewal)]
    assert len(granted) == 3
    assert all(reservation.lease_until <= item.lease_until <= deadline for item in granted)
    assert [item for item in results if isinstance(item, ErrorCode)] == [
        ErrorCode.MODEL_CALL_LEASE_LOST
    ]

    row = await _lease_row(tenant.tenant_id, reservation.call_attempt_id)
    assert row["state"] == "ACTIVE"
    assert row["lease_until"] == max(item.lease_until for item in granted)
    assert row["lease_until"] > reservation.lease_until
    assert await _counts(tenant, seed) == ((1, 1, 100), (1, 1, 100))


async def test_b222_renew_and_settle_race_settles_the_attempt_exactly_once(
    tenant: Tenant, service: ModelCapacityService
) -> None:
    """[B-222] 续租与结算在 attempt 行锁上竞争：结算恰好一次、不重复扣减、不超发。"""
    deadline = _deadline(300)
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=2, group_max_inflight=2)
    reservation = await _reserve(
        service, tenant, seed, owner=_probe(), reserved_tokens=120, deadline_at=deadline
    )
    engine, connection, transaction, xid = await _hold_attempt_row(
        tenant.tenant_id, reservation.call_attempt_id
    )
    try:
        renewing = asyncio.create_task(
            service.renew(
                tenant_id=tenant.tenant_id,
                call_attempt_id=reservation.call_attempt_id,
                epoch=reservation.epoch,
                lease_owner="rt-1",
                deadline_at=deadline,
            )
        )
        settling = asyncio.create_task(
            service.settle(
                tenant_id=tenant.tenant_id,
                call_attempt_id=reservation.call_attempt_id,
                settlement=_settlement(reservation, "NOT_SENT"),
            )
        )
        await _wait_for_lease_row_waiters(engine, xid, count=2)
        await transaction.rollback()
        renew_result, settle_result = await asyncio.gather(
            renewing, settling, return_exceptions=True
        )
    finally:
        await connection.close()
        await engine.dispose()

    assert isinstance(settle_result, CapacityRelease), settle_result
    assert settle_result.state == "SETTLED" and settle_result.released is True
    if isinstance(renew_result, BaseException):
        assert isinstance(renew_result, AppError), renew_result
        assert renew_result.code == ErrorCode.MODEL_CALL_LEASE_LOST
    else:
        assert isinstance(renew_result, CapacityRenewal), renew_result
        assert renew_result.state == "ACTIVE" and renew_result.lease_until <= deadline
    assert (await _lease_row(tenant.tenant_id, reservation.call_attempt_id))["state"] == "SETTLED"
    assert await _counts(tenant, seed) == ((0, 0, 0), (0, 0, 0))


# --------------------------------------------------------------------------------------
# 结算：完成证据、usage 结算、无 usage 保守保留、重复结算幂等
# --------------------------------------------------------------------------------------


async def test_b222_settle_success_releases_excess_reservation_by_actual_usage(
    tenant: Tenant, service: ModelCapacityService
) -> None:
    """[B-222] 成功按实际 usage 结算：释放多余预占，请求数保留（供方确实被调用过）。"""
    deadline = _deadline(300)
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=2, group_max_inflight=2)
    reservation = await _reserve(
        service, tenant, seed, owner=_probe(), reserved_tokens=500, deadline_at=deadline
    )
    settled = await service.settle(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        settlement=_settlement(
            reservation, "SUCCEEDED", usage=_usage(120, 80), proof=_proof()
        ),
    )
    assert settled.state == "SETTLED" and settled.released is True
    assert await _counts(tenant, seed) == ((0, 1, 200), (0, 1, 200))
    assert (await _lease_row(tenant.tenant_id, reservation.call_attempt_id))["state"] == "SETTLED"


async def test_b222_settle_without_usage_keeps_the_conservative_reservation(
    tenant: Tenant, service: ModelCapacityService
) -> None:
    """[B-222] 有完成证据但无 usage：在飞释放、预占按保守预算保留；重复结算不再扣减。"""
    deadline = _deadline(300)
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=2, group_max_inflight=2)
    reservation = await _reserve(
        service, tenant, seed, owner=_probe(), reserved_tokens=400, deadline_at=deadline
    )
    settlement = _settlement(reservation, "SUCCEEDED", proof=_proof())
    settled = await service.settle(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        settlement=settlement,
    )
    assert settled.state == "SETTLED" and settled.released is True
    assert await _counts(tenant, seed) == ((0, 1, 400), (0, 1, 400))

    repeat = await service.settle(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        settlement=settlement,
    )
    assert repeat.state == "SETTLED" and repeat.released is False
    released_again = await service.release_unsent(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        epoch=reservation.epoch,
        lease_owner="rt-1",
    )
    assert released_again.released is False
    assert await _counts(tenant, seed) == ((0, 1, 400), (0, 1, 400))


async def test_b222_settle_failure_without_evidence_holds_unknown_until_evidence_arrives(
    tenant: Tenant, service: ModelCapacityService
) -> None:
    """[B-222] 失败不假定供应商未计费：无完成证据保留 UNKNOWN 占用，凭证据才结算释放。"""
    deadline = _deadline(300)
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=1, group_max_inflight=1)
    reservation = await _reserve(
        service, tenant, seed, owner=_probe(), reserved_tokens=300, deadline_at=deadline
    )
    failed = await service.settle(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        settlement=_settlement(reservation, "FAILED"),
    )
    assert failed.state == "UNKNOWN" and failed.released is False
    assert await _counts(tenant, seed) == ((1, 1, 300), (1, 1, 300))
    blocked = await _reserve_or_code(
        service, tenant, seed, owner=_probe(), reserved_tokens=10, deadline_at=deadline
    )
    assert blocked == ErrorCode.MODEL_CAPACITY_UNAVAILABLE
    with pytest.raises(AppError) as unsafe:
        await service.release_unsent(
            tenant_id=tenant.tenant_id,
            call_attempt_id=reservation.call_attempt_id,
            epoch=reservation.epoch,
            lease_owner="rt-1",
        )
    assert unsafe.value.code == ErrorCode.MODEL_CALL_OUTCOME_UNKNOWN

    repeat_unknown = await service.settle(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        settlement=_settlement(reservation, "UNKNOWN"),
    )
    assert repeat_unknown.state == "UNKNOWN" and repeat_unknown.released is False
    assert await _counts(tenant, seed) == ((1, 1, 300), (1, 1, 300))

    evidenced = await service.settle(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        settlement=_settlement(
            reservation, "FAILED", proof=_proof(kind="SERVER_HARD_TIMEOUT", reference="server-hard-b")
        ),
    )
    assert evidenced.state == "SETTLED" and evidenced.released is True
    assert await _counts(tenant, seed) == ((0, 1, 300), (0, 1, 300))
    repeat_evidenced = await service.settle(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        settlement=_settlement(
            reservation, "SUCCEEDED", usage=_usage(5, 5), proof=_proof(reference="later-a")
        ),
    )
    assert repeat_evidenced.state == "SETTLED" and repeat_evidenced.released is False
    assert await _counts(tenant, seed) == ((0, 1, 300), (0, 1, 300))


async def test_b222_settle_unknown_holds_capacity_and_refuses_unsent_claims(
    tenant: Tenant, service: ModelCapacityService
) -> None:
    """[B-222] UNKNOWN 保留占用且不被当成未发送释放；结算不产生负计数。"""
    deadline = _deadline(300)
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=1, group_max_inflight=1)
    reservation = await _reserve(
        service, tenant, seed, owner=_probe(), reserved_tokens=250, deadline_at=deadline
    )
    unknown = await service.settle(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        settlement=_settlement(reservation, "UNKNOWN"),
    )
    assert unknown.state == "UNKNOWN" and unknown.released is False
    assert await _counts(tenant, seed) == ((1, 1, 250), (1, 1, 250))
    blocked = await _reserve_or_code(
        service, tenant, seed, owner=_probe(), reserved_tokens=10, deadline_at=deadline
    )
    assert blocked == ErrorCode.MODEL_CAPACITY_UNAVAILABLE
    with pytest.raises(AppError) as unsafe:
        await service.settle(
            tenant_id=tenant.tenant_id,
            call_attempt_id=reservation.call_attempt_id,
            settlement=_settlement(reservation, "NOT_SENT"),
        )
    assert unsafe.value.code == ErrorCode.MODEL_CALL_OUTCOME_UNKNOWN
    assert (await _lease_row(tenant.tenant_id, reservation.call_attempt_id))["state"] == "UNKNOWN"
    assert await _counts(tenant, seed) == ((1, 1, 250), (1, 1, 250))


async def test_b222_settle_without_quota_group_counts_only_the_key_resource(
    tenant: Tenant, service: ModelCapacityService
) -> None:
    """[B-222] 无共享额度组的 Key：结算只动 KEY 行，不为它虚构组身份、不漏结算。"""
    deadline = _deadline(300)
    seed = Seed(
        tenant_id=tenant.tenant_id,
        credential_id=await _seed_credential(tenant.tenant_id, max_inflight=2),
        key_resource_id=uuid.uuid4(),
    )
    reservation = await _reserve(
        service, tenant, seed, owner=_probe(), reserved_tokens=300, deadline_at=deadline
    )
    settled = await service.settle(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        settlement=_settlement(
            reservation, "SUCCEEDED", usage=_usage(40, 60), proof=_proof(reference="no-group-a")
        ),
    )
    assert settled.state == "SETTLED" and settled.released is True
    key_row = await _capacity_row(tenant.tenant_id, RESOURCE_KEY, seed.key_resource_id)
    assert (key_row["inflight"], key_row["reserved_requests"], key_row["reserved_tokens"]) == (
        0,
        1,
        100,
    )
    assert await _capacity_row_count(tenant.tenant_id) == 1


async def test_b222_settle_across_minute_boundary_adjusts_only_the_counted_window(
    tenant: Tenant, service: ModelCapacityService
) -> None:
    """[B-222] 跨分钟结算：边界桶仍计该预占，按实际 usage 替换后组合 TPM 计数正确。"""
    deadline = _deadline(300)
    seed = await _seed_share(
        tenant.tenant_id, key_max_inflight=5, group_max_inflight=5, tpm=1000
    )
    reservation = await _reserve(
        service, tenant, seed, owner=_probe(), reserved_tokens=500, deadline_at=deadline
    )
    await _shift_window(tenant.tenant_id, seed, minutes_ago=1)

    settled = await service.settle(
        tenant_id=tenant.tenant_id,
        call_attempt_id=reservation.call_attempt_id,
        settlement=_settlement(
            reservation, "SUCCEEDED", usage=_usage(100, 50), proof=_proof(reference="usage-a")
        ),
    )
    assert settled.state == "SETTLED" and settled.released is True
    assert await _counts(tenant, seed) == ((0, 1, 150), (0, 1, 150))

    granted = await _reserve(
        service, tenant, seed, owner=_probe(), reserved_tokens=800, deadline_at=deadline
    )
    assert granted.state == "ACTIVE"
    over = await _reserve_or_code(
        service, tenant, seed, owner=_probe(), reserved_tokens=100, deadline_at=deadline
    )
    assert over == ErrorCode.MODEL_CAPACITY_UNAVAILABLE
    assert await _counts(tenant, seed) == ((1, 2, 950), (1, 2, 950))


async def test_b222_settle_after_window_rollover_leaves_the_new_bucket_untouched(
    tenant: Tenant, service: ModelCapacityService
) -> None:
    """[B-222] 窗口已滚过：旧尝试结算只释放自己的在飞，不污染新桶、不出现负计数。"""
    deadline = _deadline(300)
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=5, group_max_inflight=5, rpm=5)
    older = await _reserve(
        service, tenant, seed, owner=_probe(), reserved_tokens=500, deadline_at=deadline
    )
    await _shift_window(tenant.tenant_id, seed, minutes_ago=2)
    await _backdate_lease(tenant.tenant_id, older.call_attempt_id, minutes_ago=3)

    newer = await _reserve(
        service,
        tenant,
        seed,
        owner=_probe(),
        reserved_tokens=100,
        deadline_at=deadline,
        call=RunCall(),
    )
    assert newer.state == "ACTIVE"
    assert await _counts(tenant, seed) == ((2, 1, 100), (2, 1, 100))

    settled = await service.settle(
        tenant_id=tenant.tenant_id,
        call_attempt_id=older.call_attempt_id,
        settlement=_settlement(
            older, "SUCCEEDED", usage=_usage(120, 30), proof=_proof(reference="usage-b")
        ),
    )
    assert settled.state == "SETTLED" and settled.released is True
    assert await _counts(tenant, seed) == ((1, 1, 100), (1, 1, 100))
    assert (await _lease_row(tenant.tenant_id, newer.call_attempt_id))["state"] == "ACTIVE"


# --------------------------------------------------------------------------------------
# ACTIVE→UNKNOWN 清扫：本地超时不释放远端未知占用
# --------------------------------------------------------------------------------------


async def test_b222_sweep_marks_expired_active_leases_unknown_without_releasing(
    tenant: Tenant, service: ModelCapacityService
) -> None:
    """[B-222] 过期 ACTIVE 清理为 UNKNOWN（有界、幂等）：保留占用等证据，绝不冒充已停。"""
    deadline = _deadline(300)
    seed = await _seed_share(tenant.tenant_id, key_max_inflight=4, group_max_inflight=4)
    expired = [
        await _reserve(
            service,
            tenant,
            seed,
            owner=_probe(),
            reserved_tokens=60,
            deadline_at=deadline,
            call=RunCall(),
        )
        for _ in range(3)
    ]
    for reservation in expired:
        await _expire_lease(tenant.tenant_id, reservation.call_attempt_id)
    fresh = await _reserve(
        service, tenant, seed, owner=_probe(), reserved_tokens=40, deadline_at=deadline
    )

    swept = await service.sweep_expired(tenant_id=tenant.tenant_id, limit=2)
    assert {item.call_attempt_id for item in swept} == {
        item.call_attempt_id for item in expired[:2]
    }
    assert all(item.state == "UNKNOWN" and item.released is False for item in swept)
    assert (await _lease_row(tenant.tenant_id, expired[2].call_attempt_id))["state"] == "ACTIVE"
    assert await _counts(tenant, seed) == ((4, 4, 220), (4, 4, 220))

    rest = await service.sweep_expired(tenant_id=tenant.tenant_id, limit=2)
    assert [item.call_attempt_id for item in rest] == [expired[2].call_attempt_id]
    assert await service.sweep_expired(tenant_id=tenant.tenant_id, limit=2) == []
    assert (await _lease_row(tenant.tenant_id, fresh.call_attempt_id))["state"] == "ACTIVE"
    assert await _counts(tenant, seed) == ((4, 4, 220), (4, 4, 220))

    blocked = await _reserve_or_code(
        service, tenant, seed, owner=_probe(), reserved_tokens=10, deadline_at=deadline
    )
    assert blocked == ErrorCode.MODEL_CAPACITY_UNAVAILABLE

    evidenced = await service.settle(
        tenant_id=tenant.tenant_id,
        call_attempt_id=expired[0].call_attempt_id,
        settlement=_settlement(
            expired[0], "FAILED", proof=_proof(kind="PROVIDER_CONFIRMED", reference="sweep-a")
        ),
    )
    assert evidenced.state == "SETTLED" and evidenced.released is True
    assert await _counts(tenant, seed) == ((3, 4, 220), (3, 4, 220))

    with pytest.raises(ValueError):
        await service.sweep_expired(tenant_id=tenant.tenant_id, limit=0)
