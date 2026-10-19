"""S-07 / E-02：Worker 多实例租约、取消杀整组、崩溃接管与离线入箱 E2E（TASK-111）。

真实边界（不 mock 任何服务、不用伪造成功回执）：

- **Worker 多实例**：TASK-107 独立栈按 profile 起两个真实 Worker 进程（各 1 个执行槽）；
- **实际 Skill 子进程组**：真 zip 包落在共享卷，Skill 脚本由 Worker 以 `start_new_session=True`
  起真实子进程（脚本自己再 spawn 孙进程，用于验证取消杀**整组**）；
- **PG 租约**：`task.task_execution` 的 `lease_owner/lease_until/attempt` 是真实持久事实，
  续租、回收、接管、失约停手全部在行上取证；
- **投递链路 / 离线入箱**：真实 Gateway API-06 受理 → Runtime 真实执行到终态 → 用户级持久收件箱
  （`channel_notification`，与终态事件同事务）→ 用户"重开"后用真实 CHAT 会话经 API-11 读取。

断言位置见各用例 docstring（场景 ID → 断言逐项对应）。
"""

from __future__ import annotations

import json
import os
import signal
import time
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.acceptance.capacity.delivery_harness import (
    PROBE_API_KEY,
    ChatSession,
    SeededDelivery,
    TaskSpec,
    bind_probe_credential,
    build_stack,
    create_datastore_handle,
    db_rows,
    db_scalar,
    exchange_chat_session,
    probe_state,
    resolve_task_spec,
    seed_delivery_control,
    set_probe_behavior,
    wait_until,
)
from tests.acceptance.capacity.stack import CapacityStack
from tests.capacity.profile import load_profile

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
WEBCHAT_ENTRY_PREFIX = "/api/v1/webchat/entries"
WEBCHAT_RUN_PREFIX = "/api/v1/webchat/runs"
TASKS_PATH = "/internal/tasks"
WAIT_TIMEOUT_SEC = 150.0
#: 子进程租约口径：心跳 1s / 租约 12s（生产默认 20s/60s 的同向缩放）。
#: 余量 12×心跳让「执行中不失约」在负载下仍稳定可观测；真失约（进程被冻结）时租约仍会在
#: 12s 内过期并被另一实例按设计回收。
TASK_LEASE_SEC = 12
TASK_HEARTBEAT_SEC = 1
WORKER_POLL_INTERVAL_SEC = 1
TASK_CANCEL_CHECK_SEC = 1
TERMINAL_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")


# --------------------------------------------------------------------------------------
# 真实 Worker/Task 驱动
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkerHarness:
    stack: CapacityStack
    seeded: SeededDelivery
    session: ChatSession
    task_spec: TaskSpec
    pid_dir: Path


def _service_headers(tenant_id: str, token: str, actor_user_id: uuid.UUID) -> dict[str, str]:
    return {
        "X-Tenant-Id": tenant_id,
        "X-Internal-Service": token,
        "X-Actor-User-Id": str(actor_user_id),
    }


def submit_task(
    worker_url: str,
    *,
    tenant_id: str,
    internal_token: str,
    seeded: SeededDelivery,
    spec: TaskSpec,
    input_data: Mapping[str, Any],
    idempotency_key: str,
) -> str:
    """真实提交（Worker `POST /internal/tasks`）：与 Runtime 立即提交同一内部接口。"""
    payload = {
        "tenant_id": tenant_id,
        "agent_id": str(seeded.agent_id),
        "actor_user_id": str(seeded.user_id),
        "intent_key": "capacity_worker_delivery",
        "skill_id": str(spec.skill_id),
        "skill_artifact_id": str(spec.artifact_id),
        "input": dict(input_data),
        "execution_snapshot": spec.snapshot,
        "snapshot_hash": _snapshot_hash(spec.snapshot),
        "idempotency_key": idempotency_key,
        "delivery_mode": "NONE",
    }
    with httpx.Client(timeout=20.0, trust_env=False) as client:
        response = client.post(
            f"{worker_url}{TASKS_PATH}",
            json=payload,
            headers=_service_headers(tenant_id, internal_token, seeded.user_id),
        )
    assert response.status_code == 200, f"Task 提交失败：{response.status_code} {response.text}"
    return str(response.json()["data"]["task_id"])


def _snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    from muad_contracts import snapshot_hash

    return snapshot_hash(dict(snapshot))


def task_row(database_url: str, task_id: str) -> Mapping[str, Any]:
    rows = db_rows(
        database_url,
        "SELECT status, attempt, lease_owner, lease_until, result_json, error_code,"
        " cancel_requested, finished_at FROM task.task_execution WHERE id = $1",
        uuid.UUID(task_id),
    )
    assert rows, f"Task 行不存在：{task_id}"
    return dict(rows[0])


def task_result(task: Mapping[str, Any]) -> Mapping[str, Any]:
    """`result_json` 是 jsonb：asyncpg 回文本，这里显式解析（坏值显式空，不猜）。"""
    raw = task.get("result_json")
    if raw is None:
        return {}
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    return parsed if isinstance(parsed, Mapping) else {}


def task_events(database_url: str, task_id: str) -> list[Mapping[str, Any]]:
    return [
        dict(row)
        for row in db_rows(
            database_url,
            "SELECT event_type, payload_json FROM task.task_event WHERE task_id = $1"
            " ORDER BY seq",
            uuid.UUID(task_id),
        )
    ]


def cancel_task(
    worker_url: str, *, tenant_id: str, internal_token: str, user_id: uuid.UUID, task_id: str
) -> Mapping[str, Any]:
    with httpx.Client(timeout=20.0, trust_env=False) as client:
        response = client.post(
            f"{worker_url}{TASKS_PATH}/{task_id}/cancel",
            headers=_service_headers(tenant_id, internal_token, user_id),
        )
    assert response.status_code == 200, f"取消失败：{response.status_code} {response.text}"
    return dict(response.json()["data"])


def wait_for_status(
    database_url: str, task_id: str, status: str, *, timeout: float = WAIT_TIMEOUT_SEC
) -> Mapping[str, Any]:
    wait_until(
        lambda: task_row(database_url, task_id)["status"] == status,
        what=f"Task 未在超时内到达 {status}（最后 {task_row(database_url, task_id)}）",
        timeout=timeout,
    )
    return task_row(database_url, task_id)


# --------------------------------------------------------------------------------------
# 栈与种子：Worker 租约口径 + 真实 Skill 包
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def worker_harness(
    tmp_path_factory: pytest.TempPathFactory, capacity_profile_path: Path
) -> Iterator[WorkerHarness]:
    """真实独立栈：两个 Worker 各 1 槽、租约 12s；Skill 包先落共享卷（种子先、start 后）。"""
    root = tmp_path_factory.mktemp("worker-delivery")
    pid_dir = root / "probe-pids"
    pid_dir.mkdir(parents=True, exist_ok=True)
    # 本栈一份**独立**库 + 独立 Redis 号位：Worker claim 不带租户谓词，共用库会与其它栈
    # 互相领走任务（串跑红、单跑绿）。
    datastore = create_datastore_handle()
    stack = build_stack(
        root=root,
        profile=load_profile(capacity_profile_path),
        datastore=datastore,
        service_env={
            "agent-worker": {
                "TASK_LEASE_SEC": str(TASK_LEASE_SEC),
                "TASK_HEARTBEAT_SEC": str(TASK_HEARTBEAT_SEC),
                "WORKER_POLL_INTERVAL_SEC": str(WORKER_POLL_INTERVAL_SEC),
                "TASK_CANCEL_CHECK_SEC": str(TASK_CANCEL_CHECK_SEC),
                "WORKER_EXECUTION_CONCURRENCY": "1",
                # 回收扫描节拍：生产默认 30s；这里压到 5s（只改"多久扫一次"，接管语义不变），
                # 让「租约过期 → 回收 → 另一实例接管」在用例预算内可稳定观测。
                "RUN_REAPER_INTERVAL_SEC": "5",
            }
        },
    )
    seeded = seed_delivery_control(
        datastore.database_url,
        stack.tenant_id,
        artifacts_root=root / "shared" / "artifacts",
        platform_settings={"task": {"max_attempts": 3, "delivery_backoff_base_sec": 1}},
    )
    stack.start()
    bind_probe_credential(
        datastore.database_url, seeded.credential_id, f"{stack.model_probe.url}/v1"
    )
    session = exchange_chat_session(stack.proxy.url, seeded.token, seeded.user_id)
    spec = resolve_task_spec(
        stack.url_of("console-platform"),
        stack.internal_token,
        tenant_id=stack.tenant_id,
        agent_id=seeded.agent_id,
        actor_user_id=seeded.user_id,
    )
    try:
        yield WorkerHarness(
            stack=stack, seeded=seeded, session=session, task_spec=spec, pid_dir=pid_dir
        )
    finally:
        stack.teardown()


def worker_instance_ids(stack: CapacityStack) -> set[str]:
    return {f"{os.uname().nodename}:{instance.pid}" for instance in stack.instances("agent-worker")}


# --------------------------------------------------------------------------------------
# S-07：并发任务分别续租（Worker 多实例）
# --------------------------------------------------------------------------------------


def test_s07_concurrent_tasks_renew_their_own_leases_on_two_workers(
    worker_harness: WorkerHarness,
) -> None:
    """[S-07] 并发任务分别续租：两个真实 Worker 各持一个任务，租约各自前进且不失约。

    真实边界与断言位置：
    ① 两个 Worker 进程（profile 副本数 × 1 槽）各领走一个任务：`lease_owner` 是两个不同实例；
    ② 执行中采样两次 `lease_until`：两者都**变晚**（真实续租，不是一次性授租）；
    ③ 期间 `attempt` 保持 0、`lease_owner` 不变（没有被回收/接管）；
    ④ 两个任务各自跑完，结果来自各自的 Skill 输入（并发不串），终态后租约被清除。
    """
    harness = worker_harness
    stack, seeded = harness.stack, harness.seeded
    worker_urls = [instance.url for instance in stack.instances("agent-worker")]
    assert len(worker_urls) >= 2, "本用例需要 Worker 多实例（profile 副本数）"
    first = submit_task(
        worker_urls[0],
        tenant_id=stack.tenant_id,
        internal_token=stack.internal_token,
        seeded=seeded,
        spec=harness.task_spec,
        input_data={"sleep_sec": 6, "probe": "alpha"},
        idempotency_key=f"lease-alpha-{uuid.uuid4().hex[:8]}",
    )
    second = submit_task(
        worker_urls[1],
        tenant_id=stack.tenant_id,
        internal_token=stack.internal_token,
        seeded=seeded,
        spec=harness.task_spec,
        input_data={"sleep_sec": 6, "probe": "beta"},
        idempotency_key=f"lease-beta-{uuid.uuid4().hex[:8]}",
    )

    def both_running() -> bool:
        rows = [task_row(stack.database_url, task_id) for task_id in (first, second)]
        owners = {str(row["lease_owner"]) for row in rows}
        return all(row["status"] == "RUNNING" for row in rows) and len(owners) == 2

    wait_until(both_running, what="两个并发任务未在超时内被两个 Worker 分别领取", timeout=60.0)
    rows_before = {task_id: task_row(stack.database_url, task_id) for task_id in (first, second)}
    owners_before = {task_id: str(row["lease_owner"]) for task_id, row in rows_before.items()}
    assert set(owners_before.values()) <= worker_instance_ids(stack), (
        f"租约持有者必须是本栈的真实 Worker 实例：{owners_before}"
    )

    time.sleep(2.0)
    rows_after = {task_id: task_row(stack.database_url, task_id) for task_id in (first, second)}
    for task_id, row in rows_after.items():
        assert row["status"] == "RUNNING", f"执行中不得提前终态：{row}"
        assert int(row["attempt"]) == 1, "未失约的任务不得被回收（attempt 必须保持首次领取值）"
        assert str(row["lease_owner"]) == owners_before[task_id], "租约持有者不得在续租窗口内易主"
        assert row["lease_until"] > rows_before[task_id]["lease_until"], (
            f"租约必须真实续租（lease_until 前进）：{rows_before[task_id]} -> {row}"
        )

    for task_id, probe_name in ((first, "alpha"), (second, "beta")):
        row = wait_for_status(stack.database_url, task_id, "COMPLETED")
        assert task_result(row)["probe"] == probe_name, f"结果串了任务：{task_result(row)}"
        assert task_result(row)["attempt"] == 1
        assert row["lease_owner"] is None and row["lease_until"] is None, "终态必须释放租约"
        assert [event["event_type"] for event in task_events(stack.database_url, task_id)].count(
            "COMPLETED"
        ) == 1, "一个任务只允许一个终态事件"


# --------------------------------------------------------------------------------------
# S-07：取消杀整组（真实 Skill 子进程组）
# --------------------------------------------------------------------------------------


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_s07_cancel_kills_the_whole_skill_process_group(
    worker_harness: WorkerHarness,
) -> None:
    """[S-07] 取消杀整组：协作取消到达后，Skill 直接子进程与它 spawn 的孙进程一起被回收。

    真实边界与断言位置：
    ① 真实 Skill 脚本自己 spawn 一个孙进程并把 pid 写进共享卷（真实进程树，不是替身）；
    ② 取消请求落 PG `cancel_requested`，Worker 在 `TASK_CANCEL_CHECK_SEC` 内协作取消；
    ③ 取消后任务落 `CANCELLED` 终态，且孙进程必须消失（整组 SIGTERM→SIGKILL，不是只杀直接子进程）；
    ④ 取消的任务不得产出 COMPLETED 事件（不把取消当成功）。
    """
    harness = worker_harness
    stack, seeded = harness.stack, harness.seeded
    pid_file = harness.pid_dir / f"cancel-{uuid.uuid4().hex[:8]}.pid"
    task_id = submit_task(
        stack.instances("agent-worker")[0].url,
        tenant_id=stack.tenant_id,
        internal_token=stack.internal_token,
        seeded=seeded,
        spec=harness.task_spec,
        input_data={"sleep_sec": 120, "pid_file": str(pid_file), "probe": "cancel-group"},
        idempotency_key=f"cancel-group-{uuid.uuid4().hex[:8]}",
    )
    wait_for_status(stack.database_url, task_id, "RUNNING", timeout=60.0)
    wait_until(pid_file.is_file, what="Skill 孙进程未在超时内写出 pid 文件", timeout=60.0)
    grandchild = int(pid_file.read_text().strip())
    assert _process_alive(grandchild), f"孙进程 {grandchild} 必须真实存活（否则断言无意义）"

    receipt = cancel_task(
        stack.instances("agent-worker")[0].url,
        tenant_id=stack.tenant_id,
        internal_token=stack.internal_token,
        user_id=seeded.user_id,
        task_id=task_id,
    )
    assert receipt["cancel_requested"] is True, receipt
    row = wait_for_status(stack.database_url, task_id, "CANCELLED", timeout=60.0)
    assert row["cancel_requested"] is True, "取消意图必须持久在行上"
    assert row["error_code"] in (None, "TASK_CANCELLED"), row

    wait_until(
        lambda: not _process_alive(grandchild),
        what=f"取消后孙进程 {grandchild} 仍存活（进程组未被整体回收）",
        timeout=30.0,
    )
    events = [event["event_type"] for event in task_events(stack.database_url, task_id)]
    assert "COMPLETED" not in events, f"取消的任务不得产出 COMPLETED：{events}"
    assert "CANCELLED" in events, f"取消必须留真实终态事件：{events}"


# --------------------------------------------------------------------------------------
# S-07 / E-02：崩溃/冻结任务按租约接管 + 旧租约不能写终态
# --------------------------------------------------------------------------------------


def test_s07_frozen_worker_task_is_taken_over_and_stale_lease_cannot_write(
    worker_harness: WorkerHarness,
) -> None:
    """[S-07][E-02] 卡住的持有者失约后由另一实例接管；旧租约不能再写终态/事件。

    真实边界与断言位置：
    ① SIGSTOP 冻结持有任务的 Worker 整组：心跳停摆 ⇒ PG 租约真实过期；
    ② 另一 Worker 的回收循环把任务置回可领取并**接管**（`attempt` 前进、`lease_owner` 换人）；
    ③ 接管者用同输入重跑（Skill 按 `MUAD_TASK_ATTEMPT` 缩短重跑时长）并落唯一终态；
    ④ SIGCONT 唤醒旧持有者：它的心跳续租失败 ⇒ 失约停手，**不能**用旧结果覆盖行；
       终态事件数保持 1、结果保持接管者的 attempt=1（旧租约不能写事件/终态）。
    """
    harness = worker_harness
    stack, seeded = harness.stack, harness.seeded
    instances = stack.instances("agent-worker")
    task_id = submit_task(
        instances[0].url,
        tenant_id=stack.tenant_id,
        internal_token=stack.internal_token,
        seeded=seeded,
        spec=harness.task_spec,
        input_data={"sleep_sec": 120, "retry_sleep_sec": 1, "probe": "takeover"},
        idempotency_key=f"takeover-{uuid.uuid4().hex[:8]}",
    )
    wait_for_status(stack.database_url, task_id, "RUNNING", timeout=60.0)
    holder = task_row(stack.database_url, task_id)
    assert int(holder["attempt"]) == 1
    holder_owner = str(holder["lease_owner"])
    frozen = next(
        instance for instance in instances if f"{os.uname().nodename}:{instance.pid}" == holder_owner
    )
    assert frozen.pgid is not None

    os.killpg(frozen.pgid, signal.SIGSTOP)
    try:
        wait_until(
            lambda: task_row(stack.database_url, task_id)["attempt"] >= 2,
            what="租约过期后任务未被回收（attempt 未前进）",
            timeout=TASK_LEASE_SEC * 3 + 30,
        )
        row = wait_for_status(stack.database_url, task_id, "COMPLETED", timeout=WAIT_TIMEOUT_SEC)
        assert int(row["attempt"]) >= 2, row
        assert str(row["lease_owner"]) != holder_owner, "接管者必须是另一个真实 Worker 实例"
        assert task_result(row)["attempt"] >= 2, "终态结果必须来自接管后的重跑"
        assert task_result(row)["probe"] == "takeover"
        events_before_resume = task_events(stack.database_url, task_id)
        assert [event["event_type"] for event in events_before_resume].count("COMPLETED") == 1
        assert any(
            event["event_type"] == "RECLAIMED" for event in events_before_resume
        ), f"回收必须留真实事件：{events_before_resume}"
    finally:
        os.killpg(frozen.pgid, signal.SIGCONT)

    # ④ 唤醒旧持有者后：它已失约，不得再写任何终态/事件。
    time.sleep(5.0)
    row_after = task_row(stack.database_url, task_id)
    assert row_after["result_json"] == row["result_json"], "旧租约不得覆盖接管者的结果"
    assert int(row_after["attempt"]) == int(row["attempt"]), "旧租约不得推进 attempt"
    assert task_events(stack.database_url, task_id) == events_before_resume, (
        "旧租约不得再写事件（失约即停手）"
    )


# --------------------------------------------------------------------------------------
# S-07：离线结果持久入箱后可重开读取
# --------------------------------------------------------------------------------------


def _notification_rows(database_url: str, tenant_id: str) -> list[Mapping[str, Any]]:
    return [
        dict(row)
        for row in db_rows(
            database_url,
            "SELECT id, seq, kind, run_id, conversation_id, entry_id, delivery_state, read_at,"
            " event_id FROM runtime.channel_notification WHERE tenant_id = $1 ORDER BY seq",
            tenant_id,
        )
    ]


def test_s07_offline_run_result_persists_into_inbox_and_is_re_readable(
    worker_harness: WorkerHarness,
) -> None:
    """[S-07] 离线结果持久入箱后可重开读取：用户不在线也拿得到终态，重开不重复、已读独立。

    真实边界与断言位置：
    ① 用户**不订阅任何 SSE**（离线）：真实受理 → Runtime 真实执行到终态；
    ② 终态与用户级持久入箱**同一事务**：通知行引用产生它的终态事件、`read_at IS NULL`；
    ③ "重开"= 用同一 Token 重新交换 CHAT 会话，经真实 Gateway API-11 读回通知：
       run/conversation/entry 可定位，`unread_count>=1`；重复读取 seq 不变（不重复入箱）；
    ④ 会话历史（API-09）能读到离线期间的 assistant 正文（结果不是只在内存里）；
    ⑤ 显式已读只改 `read_at`：`delivery_state` 不变（已读与送达是两条独立事实）。
    """
    harness = worker_harness
    stack, seeded, session = harness.stack, harness.seeded, harness.session
    set_probe_behavior(
        stack.model_probe.url,
        {"latency_ms": 50, "deltas": ["离线", "结果", "可读"]},
        api_key=PROBE_API_KEY,
    )
    with httpx.Client(
        base_url=stack.proxy.url,
        timeout=20.0,
        trust_env=False,
        cookies={"muad_chat_session": session.cookie},
        headers={"X-Chat-Context": session.context},
    ) as client:
        created = client.post(
            f"{WEBCHAT_ENTRY_PREFIX}/{seeded.entry_id}/conversations",
            headers={"Idempotency-Key": f"conv-{uuid.uuid4().hex}"},
        )
        assert created.status_code == 201, created.text
        conversation_id = str(created.json()["data"]["conversation_id"])
        message_id = f"offline-{uuid.uuid4().hex[:12]}"
        submitted = client.post(
            f"{WEBCHAT_ENTRY_PREFIX}/{seeded.entry_id}/messages",
            json={
                "conversation_id": conversation_id,
                "message_id": message_id,
                "text": "离线提问",
                "upload_ids": [],
            },
            headers={"Idempotency-Key": f"{conversation_id}:{message_id}"},
        )
        assert submitted.status_code == 202, submitted.text
        run_id = str(submitted.json()["data"]["run_id"])

    wait_until(
        lambda: db_scalar(
            stack.database_url, "SELECT status FROM runtime.run_record WHERE id = $1", uuid.UUID(run_id)
        )
        == "COMPLETED",
        what="离线 Run 未在超时内完成",
        timeout=WAIT_TIMEOUT_SEC,
    )

    wait_until(
        lambda: _notification_rows(stack.database_url, stack.tenant_id),
        what="终态未写用户级持久收件箱",
        timeout=30.0,
    )
    notification = _notification_rows(stack.database_url, stack.tenant_id)[0]
    assert str(notification["run_id"]) == run_id
    assert str(notification["conversation_id"]) == conversation_id
    assert str(notification["entry_id"]) == str(seeded.entry_id)
    assert notification["kind"] == "RUN_COMPLETED"
    assert notification["read_at"] is None, "入箱不得代替用户已读"
    assert notification["delivery_state"] == "NOT_REQUIRED", "网页持久入箱不是出站投递意图"
    terminal_event = db_scalar(
        stack.database_url,
        "SELECT count(*) FROM runtime.canonical_event WHERE id = $1 AND run_id = $2",
        notification["event_id"],
        uuid.UUID(run_id),
    )
    assert int(terminal_event) == 1, "通知必须引用产生它的真实终态事件"

    # ③ 重开（新 CHAT 会话）读回：真实 API-11。
    reopened = exchange_chat_session(stack.proxy.url, seeded.token, seeded.user_id)
    assert reopened.cookie != session.cookie, "重开必须是新的 CHAT 会话（真实重新交换）"
    with httpx.Client(
        base_url=stack.proxy.url,
        timeout=20.0,
        trust_env=False,
        cookies={"muad_chat_session": reopened.cookie},
        headers={"X-Chat-Context": reopened.context},
    ) as client:
        page = client.get("/api/v1/webchat/notifications", params={"after_seq": 0})
        assert page.status_code == 200, page.text
        first_page = page.json()["data"]
        item = first_page["items"][0]
        assert item["run_id"] == run_id and item["conversation_id"] == conversation_id
        assert item["entry_id"] == str(seeded.entry_id)
        assert first_page["unread_count"] >= 1, "离线期间的结果必须计入未读"
        repeat = client.get("/api/v1/webchat/notifications", params={"after_seq": 0})
        assert repeat.json()["data"]["items"][0]["seq"] == item["seq"], "重读不得重复入箱"
        assert repeat.json()["data"]["items"][0]["id"] == item["id"]

        # ④ 会话历史能读到离线期间的 assistant 正文。
        history = client.get(f"/api/v1/webchat/conversations/{conversation_id}/messages")
        assert history.status_code == 200, history.text
        assistant_text = "".join(
            str(message.get("text") or "")
            for message in history.json()["data"]["items"]
            if str(message.get("role") or "").upper() == "ASSISTANT"
        )
        assert "离线结果可读" in assistant_text, f"离线正文必须可重读：{assistant_text!r}"

        # ⑤ 显式已读：只改 read_at，投递状态不动。
        read = client.post(f"/api/v1/webchat/notifications/{item['id']}/read")
        assert read.status_code == 200, read.text
        assert read.json()["data"]["read_at"] is not None
    after_read = _notification_rows(stack.database_url, stack.tenant_id)[0]
    assert after_read["read_at"] is not None
    assert after_read["delivery_state"] == "NOT_REQUIRED", "已读不得改写投递回执"
    assert int(probe_state(stack.model_probe.url)["call_count"]) >= 1, "离线执行必须是真实模型调用"
