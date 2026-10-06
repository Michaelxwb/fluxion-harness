"""[S-11][E-06] 最终投递去重与重试（integration）。

不得 Mock 的真实边界：真实 Worker 子进程（真实投递循环 + 真实 PG）、真实 IM Gateway 进程、
真实 Redis（`SET NX EX` 去重 + TTL）、真实本地渠道探针（本地真实 HTTP 进程，不是替身）。

E-06 的 `delivered=false` 支：生产 Gateway **当前没有任何路径**会返回 2xx + `delivered=false`
（它的 in-flight 重放有界等待后回 5xx，见 `apps/im-gateway/.../api/delivery.py::_replay`），
故该响应形状只能由 `delivery_rewriter` 这一跳**故障注入**造出——请求仍打到真实 Gateway、
真实 Redis 与真实探针，只在**回程**延迟并改写 `delivered`。

退避口径：使用**真实间隔**（不注入时钟）——真实 Worker 进程按生产 `DeliveryLoop` 的
`delivery_backoff_base_sec × 2**delivery_attempts` 窗口重试，用例从 `task.task_event` 的真实
`create_time` 差值与库内 `delivery_attempts` 回读测量。因子套件默认 `delivery_poll_interval_sec=5`
会把每个窗口放大到 5s 的轮询粒度上，故本模块把该真实配置项钉到 1s（生产环境变量，二者皆为真实节拍）。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import datetime
from typing import Any, cast

import httpx
import pytest
import redis.asyncio
import sqlalchemy as sa
from muad_common import SharedSettings
from muad_im_gateway.api.delivery import DELIVERY_DEDUPE_PREFIX, DELIVERY_DEDUPE_TTL_SEC

from .environment import (
    INTERNAL_TOKEN,
    PLATFORM_SETTINGS,
    TENANT,
    DfxStack,
    TaskSpec,
    await_no_extra_services,
    count_task_events,
    delivery_pair,
    delivery_rewriter,
    instance_id,
    probe_deliveries,
    read_task_row,
    resolve_task_spec,
    run_async,
    run_db,
    stop_spawned_services,
    submit_task,
    swapped_workers,
)

pytestmark = pytest.mark.integration

POLL_INTERVAL_SEC = 0.2
DELIVERY_TIMEOUT_SEC = 90.0
TERMINAL_TIMEOUT_SEC = 120.0
# 退避臂的总预算：4 次失败的真实退避窗口 10+20+40+80，外加轮询粒度与负载余量。
BACKOFF_BUDGET_SEC = 300.0
# 真实轮询粒度带来的超出量：窗口只会在轮询拍点上被观察到，故实测间隔 ∈ [窗口, 窗口 + 粒度 + 余量]。
BACKOFF_SLACK_SEC = 5.0
# 「重复 delivery_key 不重复发送」的安静窗口：≥ 2 个投递轮询拍点。
QUIET_SEC = 8.0
# 响应改写代理的回程延迟：给出「投递预留已提交、投递尚未完成」的可观测窗口。
REWRITE_DELAY_SEC = 3.0
# 投递轮询粒度（生产真实配置项，覆盖默认 5s）：低于退避窗口始端的分辨率。
DELIVERY_POLL_SEC = 1
# 前 N 次渠道失败（探针注入 500），第 N+1 次成功。
DELIVERY_EVENT_TYPES = ("DELIVERY_SENT", "DELIVERY_RETRY", "DELIVERY_FAILED")


def _max_attempts() -> int:
    # 投递尝试上限的唯一来源是验收栈**种下的平台设置**（本栈未覆盖该键 ⇒ schema 默认），
    # 供用例计算"下一次尝试"。
    return PLATFORM_SETTINGS.task.delivery_max_attempts


def _backoff_base_sec() -> int:
    # 退避窗口 = base × 2**attempts；base 取验收栈种下的设置（本栈种 2；生产默认 5）。
    return PLATFORM_SETTINGS.task.delivery_backoff_base_sec


def _route(bot_id: str) -> dict[str, Any]:
    return {
        "channel": "WECOM",
        "bot_id": bot_id,
        "external_user_id": "dfx-delivery-user",
        "external_conversation_id": "dfx-delivery-conversation",
    }


@pytest.fixture(scope="module", autouse=True)
def _stop_spawned(live_stack: DfxStack) -> Iterator[None]:
    """模块收尾：停掉用例内换入/还原的服务进程（依赖 `live_stack` 以保证先于其清理执行）。"""
    yield
    stop_spawned_services()


@contextmanager
def _worker_chain(
    live_stack: DfxStack,
    tmp_path: Any,
    *,
    delay_sec: float = 0.0,
    force_not_delivered: bool = False,
) -> Iterator[tuple[str, str, str]]:
    """真实探针 + 真实 Gateway (+ 可选响应改写代理) + 换到该链路上的真实 Worker。

    产出 `(gateway_url, probe_url, worker_owner)`；`worker_owner` 是本用例驱动的那个 Worker 的
    lease 实例标识，用于断言「领走这一行的是本栈 Worker」（claim 是全局的，见 `_await_worker_claim`）。
    退出时按逆序停掉；`swapped_workers` 会把基座 Worker 原样起回来（同一端口 + 同一 env）。
    投递轮询粒度钉到 1s：真实配置项，只为让退避窗口的观测精度高于默认的 5s。
    """
    with delivery_pair(live_stack, tmp_path) as (gateway_url, probe_url):
        with ExitStack() as stack:
            worker_gateway_url = gateway_url
            if delay_sec or force_not_delivered:
                worker_gateway_url = stack.enter_context(
                    delivery_rewriter(
                        gateway_url,
                        tmp_path,
                        delay_sec=delay_sec,
                        force_not_delivered=force_not_delivered,
                    )
                )
            start = stack.enter_context(swapped_workers(live_stack, tmp_path))
            worker = start(
                "worker-delivery",
                IM_GATEWAY_URL=worker_gateway_url,
                DELIVERY_POLL_INTERVAL_SEC=str(DELIVERY_POLL_SEC),
            )
            yield gateway_url, probe_url, instance_id(worker)


def _submit(
    stack: DfxStack,
    http: httpx.Client,
    spec: TaskSpec,
    *,
    delivery_mode: str = "NONE",
    delivery_route: dict[str, Any] | None = None,
    **input_data: Any,
) -> uuid.UUID:
    """经真实 HTTP 提交一个 Task，返回 PG 里的真实 task id。"""
    submitted = submit_task(
        stack,
        http,
        spec,
        input_data=input_data,
        idempotency_key=f"dfx-delivery-{uuid.uuid4().hex}",
        delivery_mode=delivery_mode,
        delivery_route=delivery_route,
    )
    return uuid.UUID(str(submitted["task_id"]))


def _claim_owners(task_id: uuid.UUID) -> list[str]:
    """DIAG: CLAIMED 事件的 lease_owner（谁真正执行了这一行）。"""

    async def query(factory: Any) -> list[str]:
        async with factory() as session:
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT payload_json->>'lease_owner' FROM task.task_event"
                        " WHERE tenant_id = :t AND task_id = :id AND event_type = 'CLAIMED'"
                        " ORDER BY seq"
                    ),
                    {"t": TENANT, "id": task_id},
                )
            ).all()
        return [str(row[0]) for row in rows]

    return cast(list[str], run_db(query))


def _await_worker_claim(
    task_id: uuid.UUID, expected_owner: str, *, timeout_sec: float = 30.0
) -> None:
    """断言这一行由**本栈 Worker** 领取。

    claim 没有 tenant 谓词（`claimer.py::_claimable_conditions`），共享库上任何别的 Worker 都
    领得到本租户的行；外来 Worker 用它自己的 `ARTIFACT_ROOT` 执行，本栈刚种下的 Skill 包在那边
    不存在，表现为 `SKILL_ARTIFACT_UNAVAILABLE` 并被重试到 FAILED——与本用例要断言的事实无关，
    却会让用例以极难看懂的方式失败（本仓实测：本地 dev 服务 `uvicorn muad_agent_worker.main:app
    --app-dir apps/agent-worker/src --reload` 的 reload 子进程命令行不含 `-m uvicorn muad_`，
    逃过了所有孤儿进程判据）。故这里以真实库回读的 `lease_owner` 直接把环境前提钉死。
    """
    deadline = time.monotonic() + timeout_sec
    owners: list[str] = []
    while time.monotonic() < deadline:
        owners = _claim_owners(task_id)
        foreign = [owner for owner in owners if owner != expected_owner]
        assert not foreign, (
            f"本栈之外的 Worker 领取了 task {task_id}：owner={foreign} ≠ 本栈 {expected_owner}。"
            "共享库上还有别的 Worker 在跑（典型：本地 dev 服务 muad_agent_worker），"
            "验收要求独占 Worker 池——请先停掉它再跑本套件。"
        )
        if owners:
            return
        time.sleep(POLL_INTERVAL_SEC)
    raise AssertionError(f"task {task_id} 未在 {timeout_sec}s 内被任何 Worker 领取（owner={owners}）")


def _await_row(
    task_id: uuid.UUID, predicate: Any, *, what: str, timeout_sec: float
) -> dict[str, Any]:
    """有界轮询真实 PG：以绝对时刻为界，条件不成立就带着最后盘面失败。"""
    deadline = time.monotonic() + timeout_sec
    row: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        row = read_task_row(task_id)
        assert row is not None, f"PG 中不存在 task {task_id}"
        if predicate(row):
            return row
        time.sleep(POLL_INTERVAL_SEC)
    raise AssertionError(
        f"task {task_id} 未在 {timeout_sec}s 内{what}，最后盘面：{row}，"
        f"claim owners={_claim_owners(task_id)}"
    )


def _await_probe(
    probe_url: str, bot_id: str, *, count: int, timeout_sec: float
) -> list[dict[str, Any]]:
    """有界轮询真实渠道探针：等它收到至少 `count` 条本用例路由的投递。"""
    deadline = time.monotonic() + timeout_sec
    records: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        records = probe_deliveries(probe_url, bot_id)
        if len(records) >= count:
            return records
        time.sleep(POLL_INTERVAL_SEC)
    raise AssertionError(
        f"渠道探针未在 {timeout_sec}s 内收到 {count} 条投递（实收 {len(records)}）"
    )


def _assert_quiet(probe_url: str, bot_id: str, expected: list[dict[str, Any]]) -> None:
    """安静窗口：探针侧不再出现新的投递记录（去重生效的负例证据）。"""
    deadline = time.monotonic() + QUIET_SEC
    while time.monotonic() < deadline:
        assert probe_deliveries(probe_url, bot_id) == expected, "安静窗口内又出现了新的投递"
        time.sleep(POLL_INTERVAL_SEC)


def _await_delivery_retries(task_id: uuid.UUID, count: int, *, timeout_sec: float) -> None:
    """有界轮询真实 PG 的投递事件，等到累计 `count` 次 `DELIVERY_RETRY`（= 已确凿失败 count 次）。

    比「按探针注入次数对齐」可靠：`delivery_attempts` 在发起请求前自增，而某次尝试可能根本没走到探针
    （Gateway 侧 reserve 冲突等），只看计数会在慢环境下错位。
    """
    deadline = time.monotonic() + timeout_sec
    observed = 0
    while time.monotonic() < deadline:
        observed = sum(1 for kind, _ts, _payload in _delivery_events(task_id) if kind == "DELIVERY_RETRY")
        if observed >= count:
            return
        time.sleep(0.5)
    raise AssertionError(f"等待 {count} 次 DELIVERY_RETRY 超时（实测 {observed}）：task={task_id}")


def _delivery_events(task_id: uuid.UUID) -> list[tuple[str, datetime, dict[str, Any]]]:
    """本 Task 的投递事件（类型 / 真实库时间 / 载荷），按 seq 排序。"""

    async def query(factory: Any) -> list[tuple[str, datetime, dict[str, Any]]]:
        async with factory() as session:
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT event_type, create_time, payload_json FROM task.task_event"
                        " WHERE tenant_id = :t AND task_id = :id AND event_type LIKE 'DELIVERY%'"
                        " ORDER BY seq"
                    ),
                    {"t": TENANT, "id": task_id},
                )
            ).all()
        return [(str(row[0]), cast(datetime, row[1]), dict(row[2])) for row in rows]

    return cast(list[tuple[str, datetime, dict[str, Any]]], run_db(query))


def _dedupe_ttl(delivery_key: str) -> int:
    """真实 Redis 上去重键的剩余 TTL（秒；键不存在返回 -2）。"""

    async def query() -> int:
        client = redis.asyncio.Redis.from_url(SharedSettings().require_redis_url())
        try:
            return int(await client.ttl(f"{DELIVERY_DEDUPE_PREFIX}:{delivery_key}"))
        finally:
            await client.aclose()

    return cast(int, run_async(query))


def _replay(
    http: httpx.Client,
    gateway_url: str,
    task_id: uuid.UUID,
    delivery_key: str,
    bot_id: str,
    *,
    tenant_id: str = TENANT,
) -> dict[str, Any]:
    """对真实 Gateway 重放同一 `delivery_key`（真实 Redis 去重路径）。

    `tenant_id` 是**交付契约的必填字段**（交付审计的幂等键 `(tenant_id, artifact_id, route_key)`
    要靠它）；重放必须带**原租户**，否则审计会落到另一个租户名下。
    """
    response = http.post(
        f"{gateway_url}/internal/deliveries",
        headers={"X-Tenant-Id": tenant_id, "X-Internal-Service": INTERNAL_TOKEN},
        json={
            "tenant_id": tenant_id,
            "task_id": str(task_id),
            "delivery_key": delivery_key,
            "route": _route(bot_id),
            "message": {"type": "text", "text": "dfx delivery replay"},
            "artifact_ids": [],
        },
    )
    assert response.status_code == 200, f"重放必须返回 200：{response.status_code} {response.text}"
    return cast(dict[str, Any], response.json()["data"])


def _seed_delivery_attempts(task_id: uuid.UUID, attempts: int) -> None:
    """把本 Task 的投递尝试数真实种到 `attempts`，并把 `update_time` 回拨到退避窗口之外。

    终态之前写入（投递循环只选终态行），故不与真实投递竞争；回拨 `update_time` 是因为真实退避
    谓词同样作用于被种的尝试数（`5 * 2 ** attempts`），不回拨就得先干等 80s。
    """

    async def update(factory: Any) -> None:
        async with factory() as session:
            result = await session.execute(
                sa.text(
                    "UPDATE task.task_execution"
                    " SET delivery_attempts = :attempts, update_time = now() - interval '1 day'"
                    " WHERE tenant_id = :t AND id = :id"
                ),
                {"t": TENANT, "id": task_id, "attempts": attempts},
            )
            assert result.rowcount == 1, f"未种到投递尝试数：{task_id}"
            await session.commit()

    run_db(update)


def test_s11_final_delivery_dedupes_replay_and_persists_before_send(
    live_stack: DfxStack, http: httpx.Client, tmp_path: Any
) -> None:
    """[S-11] 同一 `delivery_key` 投递两次：第二次 200 且不重发；最终 SENT；结果先持久化后投递。"""
    bot_id = "dfx-delivery-s11"
    with _worker_chain(live_stack, tmp_path) as (gateway_url, probe_url, worker):
        http.post(f"{probe_url}/probe/reset")
        spec = resolve_task_spec(live_stack, http)
        task_id = _submit(
            live_stack,
            http,
            spec,
            probe="s11-persist",
            delivery_mode="FINAL_ONLY",
            delivery_route=_route(bot_id),
        )
        _await_worker_claim(task_id, worker)
        # 「先持久化后投递」：投递循环只可能选中终态行，且正文由库内结果构造
        records = _await_probe(probe_url, bot_id, count=1, timeout_sec=DELIVERY_TIMEOUT_SEC)
        persisted = read_task_row(task_id)
        assert persisted is not None
        assert persisted["status"] == "COMPLETED" and persisted["finished_at"] is not None, (
            f"投递到达时结果尚未持久化：{persisted}"
        )
        assert persisted["result_json"] == {"slept_sec": 0.0, "probe": "s11-persist"}
        assert '"probe":"s11-persist"' in str(records[0]["text"]), (
            f"投递正文不是由库内已持久化的结果构造：{records[0]['text']}"
        )

        sent = _await_row(
            task_id,
            lambda row: row["delivery_status"] == "SENT",
            what="终态按 delivery_mode=FINAL_ONLY 投递成功",
            timeout_sec=DELIVERY_TIMEOUT_SEC,
        )
        assert sent["delivery_key"] == f"task:{task_id}:final"
        assert sent["delivery_attempts"] == 1 and sent["delivered_at"] is not None
        assert cast(datetime, sent["finished_at"]) <= cast(datetime, sent["delivered_at"])
        assert count_task_events(task_id, "DELIVERY_SENT") == 1

        # 重放同一 delivery_key：真实 Gateway + 真实 Redis 去重，不重复触达渠道
        replay = _replay(http, gateway_url, task_id, str(sent["delivery_key"]), bot_id)
        assert replay["duplicate"] is True, replay
        assert replay["deduplicated"] is True and replay["delivered"] is True, replay
        _assert_quiet(probe_url, bot_id, records)
        ttl = _dedupe_ttl(str(sent["delivery_key"]))
        assert DELIVERY_DEDUPE_TTL_SEC - 120 < ttl <= DELIVERY_DEDUPE_TTL_SEC, ttl
        # 重放不得改写 Worker 侧事实
        after = read_task_row(task_id)
        assert after is not None and after["delivery_status"] == "SENT"
        assert after["delivery_attempts"] == 1 and after["delivered_at"] == sent["delivered_at"]
        print(
            f"[S-11] 正文={records[0]['text']!r} finished_at={persisted['finished_at']} "
            f"delivered_at={sent['delivered_at']} dedupe_ttl={ttl}"
        )
    await_no_extra_services()


def test_e06_backoff_sequence_then_success(live_stack: DfxStack, http: httpx.Client, tmp_path: Any) -> None:
    """[E-06] 渠道失败后按真实退避窗口重投、恢复后成功：`delivery_backoff_base_sec × 2**attempts`。

    **只断言产品不变量，不赌"第几次成功"**：
    - 早期写法（`delivery_attempts == max_attempts`，靠探针注入次数与 Worker 尝试次数对齐）在慢环境必红：
      某次 Worker 尝试可能没走到探针（Gateway 侧 reserve 冲突等）⇒ 对齐错位；而"等到已失败 N-1 次再放开"
      又可能晚于最后一次尝试（默认退避更短时）⇒ 盘面已 FAILED。
    - 现在：探针**持续失败**（数量取到上限以上）→ 观察到 ≥2 次 `DELIVERY_RETRY`（确凿的多次重试）后
      **立即放开**探针 → 断言「尝试次数 ≥3、不超过上限、事件序列 = 重试×(attempts-1) + 一次 SENT、
      各次间隔落在指数窗口内、成功触达恰好 1 次」——这些与机器快慢无关。
    """
    bot_id = "dfx-delivery-e06-backoff"
    max_attempts = _max_attempts()
    assert max_attempts >= 3, f"本用例需要至少 3 次尝试上限，当前 {max_attempts}"
    with _worker_chain(live_stack, tmp_path) as (gateway_url, probe_url, worker):
        http.post(f"{probe_url}/probe/reset")
        # 持续失败：数量取得比上限大——放开探针之前不会有任何一次成功
        http.post(f"{probe_url}/probe/fail-next", json={"count": max_attempts + 5})
        spec = resolve_task_spec(live_stack, http)
        task_id = _submit(
            live_stack,
            http,
            spec,
            probe="e06-backoff",
            delivery_mode="FINAL_ONLY",
            delivery_route=_route(bot_id),
        )
        _await_worker_claim(task_id, worker)
        # 先确凿地失败两次（拿到两个真实退避窗口），再放开——放开后下一次尝试即成功
        _await_delivery_retries(task_id, 2, timeout_sec=BACKOFF_BUDGET_SEC)
        pending = read_task_row(task_id)
        assert pending is not None and pending["delivered_at"] is None, pending
        http.post(f"{probe_url}/probe/fail-next", json={"count": 0})  # 只清注入失败，保留已收记录

        sent = _await_row(
            task_id,
            lambda row: row["delivery_status"] == "SENT",
            what="渠道恢复后投递成功",
            timeout_sec=BACKOFF_BUDGET_SEC,
        )
        attempts = cast(int, sent["delivery_attempts"])
        assert 3 <= attempts <= max_attempts, sent
        assert sent["delivered_at"] is not None
        events = _delivery_events(task_id)
        kinds = [item[0] for item in events]
        assert kinds == ["DELIVERY_RETRY"] * (attempts - 1) + ["DELIVERY_SENT"], kinds
        gaps = [
            (events[index + 1][1] - events[index][1]).total_seconds()
            for index in range(len(events) - 1)
        ]
        # 第 k 次失败后，下一次尝试的窗口是 `base * 2**k` 秒（attempts 已被自增并提交）。
        # 该 base 取自**验收栈种下的平台设置**（`PLATFORM_SETTINGS.task.delivery_backoff_base_sec`），
        # 故窗口随种下的值走，断言规律不变。
        base = _backoff_base_sec()
        for index, gap in enumerate(gaps, start=1):
            window = float(base * 2**index)
            assert window <= gap <= window + BACKOFF_SLACK_SEC, (
                f"第 {index} 次失败后的退避间隔 {gap}s 不在 [{window}, {window + BACKOFF_SLACK_SEC}]"
            )
        # 失败期间渠道收不到（探针不记录），成功只发生一次 → 触达恰好 1 次
        records = probe_deliveries(probe_url, bot_id)
        assert len(records) == 1, f"同一 delivery_key 不得重复成功发送：{records}"
        assert count_task_events(task_id, "DELIVERY_SENT") == 1
        windows = [base * 2**index for index in range(1, attempts)]
        print(f"[E-06] 退避实测间隔={gaps}s 窗口={windows}s attempts={attempts}")
    await_no_extra_services()


def test_e06_placeholder_200_is_never_sent_and_exhaustion_writes_audit(
    live_stack: DfxStack, http: httpx.Client, tmp_path: Any
) -> None:
    """[E-06] 2xx + `delivered=false` 不得置 SENT；超限置 FAILED 并留审计；去重键不重发。"""
    bot_id = "dfx-delivery-e06-placeholder"
    max_attempts = _max_attempts()
    with _worker_chain(
        live_stack,
        tmp_path,
        delay_sec=REWRITE_DELAY_SEC,
        force_not_delivered=True,
    ) as (gateway_url, probe_url, worker):
        http.post(f"{probe_url}/probe/reset")
        spec = resolve_task_spec(live_stack, http)
        task_id = _submit(
            live_stack,
            http,
            spec,
            sleep_sec=3,
            probe="e06-placeholder",
            delivery_mode="FINAL_ONLY",
            delivery_route=_route(bot_id),
        )
        _await_worker_claim(task_id, worker)
        # 最后一次尝试：真实库种入 attempts=max-1，故本次失败即耗尽
        _seed_delivery_attempts(task_id, max_attempts - 1)
        records = _await_probe(probe_url, bot_id, count=1, timeout_sec=DELIVERY_TIMEOUT_SEC)
        # 预留先于发送：探针已收到请求，而 Worker 仍被回程延迟挡住 → 库内已提交自增与 PENDING
        in_flight = read_task_row(task_id)
        assert in_flight is not None
        assert in_flight["delivery_attempts"] == max_attempts, (
            f"delivery_attempts 未在发起请求前自增并提交：{in_flight}"
        )
        assert in_flight["delivery_status"] == "PENDING", (
            f"投递请求已发出但库内不是预留态：{in_flight}"
        )

        failed = _await_row(
            task_id,
            lambda row: row["delivery_status"] == "FAILED",
            what="占位响应按可重试失败处理并在耗尽后置 FAILED",
            timeout_sec=DELIVERY_TIMEOUT_SEC,
        )
        assert failed["status"] == "COMPLETED", "投递失败不得吞掉 Task 自身的终态事实"
        assert failed["delivered_at"] is None, "200 + delivered=false 不得写成已送达"
        assert failed["delivery_attempts"] == max_attempts
        assert count_task_events(task_id, "DELIVERY_SENT") == 0
        events = _delivery_events(task_id)
        assert [item[0] for item in events] == ["DELIVERY_FAILED"], [item[0] for item in events]
        payload = events[0][2]
        assert payload.get("terminal") is True, payload
        assert payload.get("delivery_attempts") == max_attempts, payload
        assert "not delivered" in str(payload.get("error")), payload
        # 审计载荷不得夹带任何明文密钥
        serialized = str(payload)
        for secret in (
            SharedSettings().internal_service_token or "internal_service_token",
            "dfx-reliability-model-key",
        ):
            assert secret not in serialized, f"审计载荷出现明文密钥：{payload}"

        # 成功键已在真实 Redis：重放同一 delivery_key 返回 200 且不重复触达渠道
        replay = _replay(http, gateway_url, task_id, str(failed["delivery_key"]), bot_id)
        assert replay["duplicate"] is True and replay["deduplicated"] is True, replay
        _assert_quiet(probe_url, bot_id, records)
        print(f"[E-06] 预留窗口盘面={in_flight} 审计载荷={payload} 重放={replay}")
    await_no_extra_services()


def test_e06_none_mode_produces_no_delivery_fact(
    live_stack: DfxStack, http: httpx.Client, tmp_path: Any
) -> None:
    """[E-06] `delivery_mode` 仅 `FINAL_ONLY`/`NONE`：`NONE` 不产生任何投递事实（正对照同窗口）。"""
    deliverable_bot = "dfx-delivery-e06-deliverable"
    silent_bot = "dfx-delivery-e06-silent"
    with _worker_chain(live_stack, tmp_path) as (_gateway_url, probe_url, worker):
        http.post(f"{probe_url}/probe/reset")
        spec = resolve_task_spec(live_stack, http)
        deliverable_id = _submit(
            live_stack,
            http,
            spec,
            probe="e06-deliverable",
            delivery_mode="FINAL_ONLY",
            delivery_route=_route(deliverable_bot),
        )
        silent_id = _submit(
            live_stack,
            http,
            spec,
            probe="e06-silent",
            delivery_mode="NONE",
        )
        _await_worker_claim(deliverable_id, worker)
        _await_worker_claim(silent_id, worker)
        # 正对照：投递循环与链路在同一窗口内确实在跑（否则负例是空转）
        records = _await_probe(
            probe_url, deliverable_bot, count=1, timeout_sec=DELIVERY_TIMEOUT_SEC
        )
        _await_row(
            deliverable_id,
            lambda row: row["delivery_status"] == "SENT",
            what="正对照样本投递成功",
            timeout_sec=DELIVERY_TIMEOUT_SEC,
        )
        silent = _await_row(
            silent_id,
            lambda row: row["status"] == "COMPLETED",
            what="NONE 样本执行到终态",
            timeout_sec=TERMINAL_TIMEOUT_SEC,
        )
        _assert_quiet(probe_url, deliverable_bot, records)
        assert silent["delivery_mode"] == "NONE"
        assert silent["delivery_status"] == "NONE" and silent["delivery_attempts"] == 0
        assert silent["delivered_at"] is None
        for event_type in DELIVERY_EVENT_TYPES:
            assert count_task_events(silent_id, event_type) == 0, event_type
        assert probe_deliveries(probe_url, silent_bot) == []
        print(f"[E-06] NONE 盘面={silent} 正对照成功投递={len(records)} 条")
    await_no_extra_services()
