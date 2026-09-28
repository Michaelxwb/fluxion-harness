"""[S-04][RULE-worker-001] Worker lease/claim 的可靠性验收（integration）。

真实边界：**真实 PostgreSQL**（迁移到 head；行锁、`FOR UPDATE SKIP LOCKED`、CAS 只在 PG 上成立）
+ **真实 Redis**（既有栈的外部依赖，本层不替身）+ 真实 Console/Worker uvicorn 子进程。
租约盘面一律从 PG 逐行回读（`lease_owner`/`lease_until`/`status`/`heartbeat_at`/`attempt`），
不以日志、返回值或进程状态代替。

覆盖（S-04）：同一 Task 并发 claim 只一个赢家且落败者不执行；执行中 heartbeat 续租使
`lease_until` 前移；跨租户查询不可见。另含 RULE-worker-001 在本任务可机检的口径：
claim 的 `FOR UPDATE SKIP LOCKED` 语义（行被他人事务持锁时必须跳过而非等待）、PG 为唯一
权威源而 Redis 仅 hint、`task_type` 仅 SKILL/BATCH。

不覆盖（归 TASK-005，勿在此宣称）：进入 WAITING 释放 lease、deadline 到期 sweep、
lease 过期后的 reclaim 场景。本用例不 mock 任何真实边界。
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest
import sqlalchemy as sa
from muad_agent_worker.worker.claimer import TaskClaimer
from muad_common import SharedSettings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .environment import (
    CROSS_TENANT,
    DfxStack,
    TaskSpec,
    count_rows,
    count_task_events,
    list_task_types,
    read_task_row,
    resolve_task_spec,
    run_async,
    submit_task,
    task_type_values,
    worker_instance_id,
)

pytestmark = pytest.mark.integration

# 竞态样本用长租约：竞态样板不该在本用例内被别人 reclaim 掉。
RACE_LEASE_SEC = 300
RACE_CLAIMERS = ("dfx-claimer-a", "dfx-claimer-b")
# 占住 Worker 单循环的时长 / 心跳观测窗口内 Skill 的执行时长（秒）。
# 执行时长必须显著大于心跳观测窗口（HEARTBEAT_SAMPLES × HEARTBEAT_SAMPLE_GAP_SEC = 4.5s），
# 使负载下 sleep 超时也不会让采样期间执行自己结束。
BLOCKER_SLEEP_SEC = 12.0
LOCK_BLOCKER_SLEEP_SEC = 6.0
HEARTBEAT_SLEEP_SEC = 15.0
# 心跳采样：窗口 ≥ 2 个心跳间隔（子进程 `TASK_HEARTBEAT_SEC=1`），逐点核对「租约前移 + 归属不变」。
HEARTBEAT_SAMPLES = 3
HEARTBEAT_SAMPLE_GAP_SEC = 1.5
# 行被他人事务持锁时：若 claim 不是 SKIP LOCKED，它会阻塞到此处被 PG 取消（对照样本用它证明锁真实存在）。
LOCK_STATEMENT_TIMEOUT_MS = 2000
POLL_INTERVAL_SEC = 0.2
# 等待真实 PG 行到达某状态的窗口：负载下 claim/执行会被拖慢，留足余量而不是立刻断言。
RUNNING_TIMEOUT_SEC = 60.0
COMPLETED_TIMEOUT_SEC = 120.0


def _database_url() -> str:
    return SharedSettings().require_database_url()


def _redis_url() -> str:
    return SharedSettings().require_redis_url()


def _await_status(task_id: uuid.UUID, status: str, *, timeout_sec: float) -> dict[str, Any]:
    """有界轮询真实 PG：以绝对时刻为界，不做无界 sleep。"""
    deadline = time.monotonic() + timeout_sec
    row: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        row = read_task_row(task_id)
        assert row is not None, f"PG 中不存在 task {task_id}"
        if row["status"] == status:
            return row
        time.sleep(POLL_INTERVAL_SEC)
    raise AssertionError(f"task {task_id} 未在 {timeout_sec}s 内到达 {status}，最后盘面：{row}")


def _new_key(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _submit(live_stack: DfxStack, http: httpx.Client, spec: TaskSpec, **input_data: Any) -> uuid.UUID:
    submitted = submit_task(
        live_stack,
        http,
        spec,
        input_data=input_data,
        idempotency_key=_new_key("dfx-reliability"),
    )
    return uuid.UUID(str(submitted["task_id"]))


async def _race_two_claimers() -> list[uuid.UUID | None]:
    """两条独立连接/事务上的生产 `TaskClaimer` 同时抢同一个 Task。

    并发窗口靠事件循环内的 `asyncio.gather` 制造，落败者的判据是 PG 行锁
    （`FOR UPDATE SKIP LOCKED`）——不是靠先后顺序。
    """
    engines = [create_async_engine(_database_url()) for _ in RACE_CLAIMERS]
    settings = SharedSettings(task_lease_sec=RACE_LEASE_SEC)
    try:
        claimers = [
            TaskClaimer(async_sessionmaker(engine, expire_on_commit=False), settings)
            for engine in engines
        ]
        results = await asyncio.gather(
            *(claimer.claim_one(name) for claimer, name in zip(claimers, RACE_CLAIMERS, strict=True))
        )
    finally:
        for engine in engines:
            await engine.dispose()
    return [None if item is None else item.id for item in results]


async def _claim_while_row_locked(
    task_id: uuid.UUID,
) -> dict[str, Any]:
    """行被另一事务 FOR UPDATE 持锁时，claim 必须跳过（返回 None）而不是等待。

    同时跑一个对照：同一行上不带 `skip_locked` 的锁等待会被 statement_timeout 取消，
    以此证明「行确实被锁住」——使 claim 的 None 不可能由「行不可见/不可领」蒙对。
    """
    holder = create_async_engine(_database_url())
    blocked_engine = create_async_engine(
        _database_url(),
        connect_args={"server_settings": {"statement_timeout": str(LOCK_STATEMENT_TIMEOUT_MS)}},
    )
    claimer_engine = create_async_engine(
        _database_url(),
        connect_args={"server_settings": {"statement_timeout": str(LOCK_STATEMENT_TIMEOUT_MS)}},
    )
    outcome: dict[str, Any] = {}
    try:
        async with holder.connect() as locked:
            locked_row = (
                await locked.execute(
                    sa.text("SELECT id FROM task.task_execution WHERE id = :id FOR UPDATE"),
                    {"id": task_id},
                )
            ).scalar_one()
            outcome["locked_row"] = locked_row
            control = None
            try:
                async with blocked_engine.connect() as waist:
                    await waist.execute(
                        sa.text("SELECT id FROM task.task_execution WHERE id = :id FOR UPDATE"),
                        {"id": task_id},
                    )
                control = "not-blocked"
            except sa.exc.DBAPIError as exc:
                control = type(exc.orig).__name__
            outcome["control_block"] = control
            claimer = TaskClaimer(
                async_sessionmaker(claimer_engine, expire_on_commit=False), SharedSettings()
            )
            started = time.monotonic()
            try:
                outcome["claimed"] = await claimer.claim_one("dfx-claimer-locked")
            except sa.exc.DBAPIError as exc:
                # 不是 SKIP LOCKED 时会被 statement_timeout 取消：把异常当作盘面证据登记。
                outcome["claimed"] = f"ERROR:{type(exc.orig).__name__}:{str(exc.orig)[:120]}"
            outcome["elapsed"] = time.monotonic() - started
    finally:
        for engine in (claimer_engine, blocked_engine, holder):
            await engine.dispose()
    return outcome


async def _redis_round_trip() -> dict[str, Any]:
    """真实 Redis 的 `SET NX EX` + TTL 往返（dedupe/hint 的原语，不是替身）。"""
    import redis.asyncio as redis

    client = redis.from_url(_redis_url(), decode_responses=True)
    key = f"dfx-reliability:probe:{uuid.uuid4().hex}"
    try:
        ping = await client.ping()
        first = await client.set(key, "1", nx=True, ex=5)
        second = await client.set(key, "1", nx=True, ex=5)
        ttl = await client.ttl(key)
        return {"ping": bool(ping), "first": bool(first), "second": bool(second), "ttl": int(ttl)}
    finally:
        await client.delete(key)
        await client.aclose()


async def _write_phantom_hints(task_id: uuid.UUID) -> None:
    """对不存在的 Task 写 cancel hint + 发 wakeup hint：Redis 只能提示，不能造事实。"""
    import redis.asyncio as redis

    client = redis.from_url(_redis_url(), decode_responses=True)
    try:
        await client.setex(f"task:cancel:{task_id}", 1800, "1")
        await client.publish("task:wakeup", "1")
        await asyncio.sleep(0.3)
    finally:
        await client.aclose()


def _spec(live_stack: DfxStack, http: httpx.Client) -> TaskSpec:
    """每次取冻结定义：Console resolve → 契约构建器（与 Runtime 提交同一口径）。"""
    return resolve_task_spec(live_stack, http)


def test_s04_single_claim_race_has_exactly_one_winner(
    live_stack: DfxStack, http: httpx.Client
) -> None:
    """同一 Task 并发 claim：只有一个赢家，落败者与空闲下来的 Worker 都不执行它。"""
    spec = _spec(live_stack, http)
    # 先让 Worker 单循环忙于一个长执行任务，使下面的竞态窗口内它不可能参与 claim。
    blocker_id = _submit(live_stack, http, spec, sleep_sec=BLOCKER_SLEEP_SEC, probe="blocker")
    _await_status(blocker_id, "RUNNING", timeout_sec=RUNNING_TIMEOUT_SEC)

    race_id = _submit(live_stack, http, spec, sleep_sec=0, probe="race")
    # 前置条件：这一行在两条 claimer 起跑前必须仍可领。claim 是全局的（无 tenant 谓词），
    # 若此刻另有 Worker 在跑（别的套件/未退净的进程），它会先领走这一行，本用例就不再是
    # 「本用例驱动的两者之争」——先失败并给出盘面，而不是把环境问题算成竞态结果不符。
    pre = cast(dict[str, Any], read_task_row(race_id))
    assert pre["status"] == "QUEUED" and pre["lease_owner"] is None, (
        f"竞态样板已被本用例之外的执行者领取（claim 跨租户全局有效）：{pre}"
    )
    claimed = run_async(_race_two_claimers)

    winners = [item for item in claimed if item is not None]
    losers = [index for index, item in enumerate(claimed) if item is None]
    assert winners == [race_id], f"并发 claim 的赢家集合不符（期望仅 {race_id}）：{claimed}"
    assert len(losers) == 1, f"必须恰好一个落败者，实际：{claimed}"

    row = read_task_row(race_id)
    assert row is not None
    assert row["status"] == "RUNNING"
    assert row["lease_owner"] in RACE_CLAIMERS, f"租约归属不符：{row['lease_owner']}"
    assert row["lease_until"] is not None and row["lease_until"] > datetime.now(UTC)
    assert row["attempt"] == 1, "竞态只应产生一次 claim（attempt 是 PG 权威计数）"
    assert count_task_events(race_id, "CLAIMED") == 1, "落败者又写了一次 CLAIMED 事件"
    assert row["result_json"] is None and row["finished_at"] is None

    # 等 Worker 空闲下来：它仍不能碰被租出去的这一行（落败者没有执行）。
    _await_status(blocker_id, "COMPLETED", timeout_sec=COMPLETED_TIMEOUT_SEC)
    after = read_task_row(race_id)
    assert after == row, f"Worker 空闲后改动了竞态样板：{after}"
    assert count_task_events(race_id, "CLAIMED") == 1
    assert after["status"] == "RUNNING" and after["result_json"] is None
    # 本租户此刻只有这一个被租出去的 RUNNING 行 —— 唯一租约。
    assert count_rows("task.task_execution", live_stack.tenant_id, "status = 'RUNNING'") == 1
    assert count_rows("task.task_execution", live_stack.tenant_id, "lease_owner IS NOT NULL") == 1


def test_s04_heartbeat_renews_lease_until_in_pg(live_stack: DfxStack, http: httpx.Client) -> None:
    """执行中 heartbeat 续租：`lease_until` 在真实 PG 里持续前移，且租约**一次都没被回收**。

    不变式取自 design §2.4.2 S-04（heartbeat 续租）与 §3.4.4 / `harness-worker#RULE-worker-001`
    （PG 为 lease 权威源、租约守卫式 CAS）：执行期内 `lease_owner` 始终是本基座 Worker 子进程，
    PG 无 `RECLAIMED` 事件，且 `lease_until` 在观测窗口内严格前移。**不用 `attempt == 1` 当判据**：
    租约一旦过期，`WorkerLoop._requeue_expired` 就会按设计回收、下一次 claim 递增 `attempt` ——
    那是产品在正确工作（`_cas` 的 `lease_until > now` 守卫正是为此），不是 heartbeat 失效；
    负载下的一次续租延误就会误判。`attempt` 是派生计数，本用例断言它背后的不变式本身。
    """
    spec = _spec(live_stack, http)
    task_id = _submit(live_stack, http, spec, sleep_sec=HEARTBEAT_SLEEP_SEC, probe="heartbeat")

    running = _await_status(task_id, "RUNNING", timeout_sec=RUNNING_TIMEOUT_SEC)
    lease_owner = running["lease_owner"]
    first = running["lease_until"]
    first_heartbeat = running["heartbeat_at"]
    assert lease_owner, "执行中必须有 lease_owner（claim 落库事实）"
    assert first is not None and first_heartbeat is not None
    # 租约归属必须是本基座真实 Worker 子进程（`hostname:pid`），而非测试进程或别的 Worker。
    assert lease_owner == worker_instance_id(live_stack), f"租约不属于本基座 Worker 子进程：{lease_owner}"
    host, _, pid = str(lease_owner).partition(":")
    assert host == socket.gethostname(), f"lease_owner 不是本机 Worker 实例：{lease_owner}"
    assert pid.isdigit() and int(pid) != os.getpid(), "租约不是子进程持有的"

    # 窗口 ≥ 2 个心跳间隔（`TASK_HEARTBEAT_SEC=1` 在子进程环境里），逐点核对执行者与租约。
    samples: list[dict[str, Any]] = [running]
    for _ in range(HEARTBEAT_SAMPLES):
        time.sleep(HEARTBEAT_SAMPLE_GAP_SEC)
        row = read_task_row(task_id)
        assert row is not None, f"PG 中不存在 task {task_id}"
        samples.append(row)

    for row in samples:
        assert row["status"] == "RUNNING", f"观测窗口内执行已中断：{row}"
        assert row["lease_owner"] == lease_owner, f"租约归属发生变化（被回收后重领）：{row}"
        assert row["lease_until"] is not None, "heartbeat 停摆会让租约被清空（reclaim）"

    leases = [cast(datetime, row["lease_until"]) for row in samples]
    assert leases == sorted(leases), f"lease_until 未单调前移：{leases}"
    assert leases[-1] > cast(datetime, first), f"heartbeat 未续租：lease_until {first} → {leases[-1]}"
    assert samples[-1]["heartbeat_at"] > first_heartbeat, "heartbeat_at 未前移"
    assert count_task_events(task_id, "RECLAIMED") == 0, "执行中租约被回收（heartbeat 没守住租约）"

    completed = _await_status(task_id, "COMPLETED", timeout_sec=COMPLETED_TIMEOUT_SEC)
    assert count_task_events(task_id, "RECLAIMED") == 0, "整个执行期都不该发生 reclaim"
    assert completed["lease_owner"] is None, "终态必须释放租约"
    assert completed["lease_until"] is None
    assert completed["result_json"] is not None


def test_s04_cross_tenant_query_returns_nothing(live_stack: DfxStack, http: httpx.Client) -> None:
    """跨租户查询同一个 task id 必须查不到任何行（本租户正对照）与任何关联事实。"""
    spec = _spec(live_stack, http)
    task_id = _submit(live_stack, http, spec, sleep_sec=0, probe="isolation")

    assert count_rows("task.task_execution", live_stack.tenant_id, "id = :id", {"id": task_id}) == 1
    assert read_task_row(task_id) is not None

    other_tenants = (CROSS_TENANT, f"{live_stack.tenant_id}-other")
    for other in other_tenants:
        assert read_task_row(task_id, tenant_id=other) is None
        assert count_rows("task.task_execution", other, "id = :id", {"id": task_id}) == 0
        assert count_rows("task.task_event", other, "task_id = :id", {"id": task_id}) == 0
        assert count_rows("task.task_submission", other, "task_id = :id", {"id": task_id}) == 0

    # 正对照：同一 id 在本租户确实可见（排除「查什么都为空」的蒙对）。
    assert count_task_events(task_id, "CREATED") == 1


def test_s04_claim_skips_row_locked_by_another_transaction(
    live_stack: DfxStack, http: httpx.Client
) -> None:
    """claim 是 `FOR UPDATE SKIP LOCKED`：行被他人事务持锁时必须跳过，而不是等待或重复领取。"""
    spec = _spec(live_stack, http)
    blocker_id = _submit(live_stack, http, spec, sleep_sec=LOCK_BLOCKER_SLEEP_SEC, probe="lock")
    _await_status(blocker_id, "RUNNING", timeout_sec=RUNNING_TIMEOUT_SEC)

    task_id = _submit(live_stack, http, spec, sleep_sec=0, probe="locked-row")
    assert cast(dict[str, Any], read_task_row(task_id))["status"] == "QUEUED"

    outcome = cast(dict[str, Any], run_async(lambda: _claim_while_row_locked(task_id)))
    assert outcome["locked_row"] == task_id, "持锁的目标行不对"
    assert outcome["control_block"] != "not-blocked", (
        "对照样本未被阻塞：行根本没被锁住，本用例会变成空断言"
    )
    assert outcome["claimed"] is None, "行被持锁时 claim 不该领到它（SKIP LOCKED 语义）"
    assert outcome["elapsed"] < LOCK_STATEMENT_TIMEOUT_MS / 1000, (
        f"claim 等待了 {outcome['elapsed']:.2f}s —— 说明它没有 SKIP LOCKED"
    )

    after = cast(dict[str, Any], read_task_row(task_id))
    assert after["status"] == "QUEUED" and after["lease_owner"] is None
    assert count_task_events(task_id, "CLAIMED") == 0


def test_s04_pg_is_authority_and_redis_is_hint_only(
    live_stack: DfxStack, http: httpx.Client
) -> None:
    """RULE-worker-001 口径：PG 唯一权威、Redis 仅 wake-up/cancel hint、task_type 仅 SKILL/BATCH。"""
    probe = cast(dict[str, Any], run_async(_redis_round_trip))
    assert probe["ping"] is True, "Redis 不可达：本层要求真实 Redis"
    assert probe["first"] is True and probe["second"] is False, "SET NX 语义不符"
    assert 0 < probe["ttl"] <= 5, f"EX 未生效：ttl={probe['ttl']}"

    # 真实进程链起得来：Console /healthz、Worker /readyz（含 Redis hint 模式）。
    health = http.get(f"{live_stack.console_url}/healthz")
    assert health.status_code == 200, health.text
    ready = http.get(f"{live_stack.worker_url}/readyz")
    assert ready.status_code == 200, ready.text
    assert ready.json()["data"]["wakeup_hint"] == "redis", "Worker 未接上真实 Redis（降级为扫描）"

    # Redis 只能提示：对不存在的 Task 写 cancel hint + 发 wakeup hint，PG 里既无行也无变化。
    phantom = uuid.uuid4()
    before = count_rows("task.task_execution", live_stack.tenant_id)
    run_async(lambda: _write_phantom_hints(phantom))
    assert read_task_row(phantom) is None, "Redis hint 造出了 Task —— PG 不再是权威源"
    assert count_rows("task.task_execution", live_stack.tenant_id) == before
    run_async(lambda: _drop_phantom_hint(phantom))

    # task_type 仅 SKILL/BATCH：枚举封闭 + PG 实际取值。
    assert task_type_values() == {"SKILL", "BATCH"}
    assert set(list_task_types()) <= {"SKILL", "BATCH"}, f"PG 出现未登记 task_type：{list_task_types()}"
    assert set(list_task_types(live_stack.tenant_id)) <= {"SKILL", "BATCH"}


async def _drop_phantom_hint(task_id: uuid.UUID) -> None:
    """清掉本用例写入的 hint（收尾核对不留残留 key）。"""
    import redis.asyncio as redis

    client = redis.from_url(_redis_url(), decode_responses=True)
    try:
        await client.delete(f"task:cancel:{task_id}")
    finally:
        await client.aclose()
