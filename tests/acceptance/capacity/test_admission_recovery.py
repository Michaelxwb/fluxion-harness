"""[E-01][E-15] 受理幂等与外部副作用重试安全的真实执行链路（TASK-110）。

真实边界（全部来自 `capacity_execution_stack` 的四单元独立栈，不 mock 业务服务）：
- **两并发请求 + 多 Runtime**：同一份 `POST /v1/runs` 并发打到两个真实 Runtime 子进程；
- **PG**：Run/提交/租约/Task 行都在本栈自己的空库（`muad_acc_*` + alembic head）里回读；
- **模型探针计数**：凭据指向 TASK-106 真实 HTTP 探针子进程，重投是否真的只执行一次由
  socket 层的 `key_calls` 计数裁决；
- **真实 Worker 子进程 + PG claim/reclaim**：Task 经 Worker 的受理边界落 PG，由真实 Worker
  进程 claim/执行/续租/回收；
- **受控外部写 HTTP 服务**：Skill 子进程用 `urllib` 对用例内的真实 HTTP 服务发起写与核查，
  业务键与外部效果都由服务在协议层记录。

口径：Task 的 `budget.retry_safety` 是 Owner 侧一次冻结的可信键（设计 §3.6），Worker 只读它；
未声明/未知一律 NON_RETRYABLE——本文件的提交体显式给出，不靠默认。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import uuid
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from tests.acceptance.capacity.execution_support import (
    CredentialSpec,
    TenantSeed,
    build_run_payload,
    build_task_body,
    cleanup_tenant,
    fetch_one,
    http_client,
    post_run,
    probe_control,
    probe_state,
    seed_model_pool,
    seed_tenant,
    service_headers,
    submit_task,
    task_events,
    task_row,
    wait_run_terminal,
    wait_until,
    write_skill_package,
)
from tests.acceptance.capacity.stack import CapacityStack

pytestmark = pytest.mark.e2e

NO_MEMORY_AGENT: dict[str, Any] = {"max_model_retries": 0, "memory_write": False}
TERMINAL_TASK_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")
TASK_WAIT_SEC = 60.0
#: 真实 Skill 脚本：先核查已有外部结果，再写；`lose_first_response` 模拟"外部成功但应答丢失"。
EXTERNAL_WRITE_SCRIPT = """
import json
import os
import sys
import time
import urllib.request


def _request(method, url, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


payload = json.loads(sys.stdin.read() or "{}")
probe = payload["probe_url"]
key = os.environ["MUAD_TASK_IDEMPOTENCY_KEY"]
attempt = int(os.environ["MUAD_TASK_ATTEMPT"])
if payload.get("pid_file"):
    open(payload["pid_file"], "w").write(str(os.getpid()))
seen = _request("GET", f"{probe}/effects?key={key}&attempt={attempt}")
if payload["op"] == "write" and not seen["applied"]:
    _request("POST", f"{probe}/effects", {"business_key": key, "attempt": attempt})
if attempt == 1 and payload.get("lose_first_response"):
    os._exit(7)
if payload.get("block_sec"):
    time.sleep(payload["block_sec"])
print(json.dumps({"ok": True, "attempt": attempt, "business_key": key, "reused": bool(seen["applied"])}))
"""


class EffectProbe:
    """受控外部写 HTTP 服务：真实 socket，记录外部写与崩溃后核查（业务键原样回读）。"""

    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []
        self.reads: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        probe = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 接口
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                with probe._lock:
                    probe.writes.append(dict(payload))
                self._respond({"ok": True})

            def do_GET(self) -> None:  # noqa: N802
                query = parse_qs(urlparse(self.path).query)
                entry = {
                    "business_key": (query.get("key") or [""])[0],
                    "attempt": int((query.get("attempt") or ["0"])[0]),
                }
                with probe._lock:
                    probe.reads.append(entry)
                    applied = any(
                        row.get("business_key") == entry["business_key"] for row in probe.writes
                    )
                self._respond({"applied": applied})

            def _respond(self, payload: Mapping[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@dataclass(frozen=True, slots=True)
class TaskFixture:
    """Task 提交现场：真实 Skill 包 + 真实提交体 + 业务键。"""

    body: dict[str, Any]
    idempotency_key: str
    skill_id: uuid.UUID
    artifact_id: uuid.UUID


@pytest.fixture(scope="module")
def tenant(capacity_execution_stack: CapacityStack) -> Iterator[TenantSeed]:
    """模块级租户现场；收尾按租户清干净（claim 不带租户谓词，残留会被别的栈领取）。"""
    stack = capacity_execution_stack
    seeded = seed_tenant(stack.database_url, tenant_id=f"capacity-recovery-{uuid.uuid4().hex[:8]}")
    try:
        yield seeded
    finally:
        cleanup_tenant(stack.database_url, seeded.tenant_id)


@pytest.fixture()
def http() -> Iterator[httpx.Client]:
    with http_client(timeout_sec=90.0) as client:
        yield client


@pytest.fixture()
def effect_probe() -> Iterator[EffectProbe]:
    probe = EffectProbe()
    try:
        yield probe
    finally:
        probe.close()


def _seed_pool(stack: CapacityStack, tenant: TenantSeed) -> Any:
    return seed_model_pool(
        stack.database_url,
        tenant_id=tenant.tenant_id,
        user_id=tenant.user_id,
        probe_url=stack.model_probe.url,
        credentials=(CredentialSpec(key="capacity-idem-key", api_key="sk-capacity-idem", max_inflight=1),),
        agent_runtime_config=NO_MEMORY_AGENT,
    )


# --------------------------------------------------------------------------------------
# E-01：同消息并发重投只受理一个 Run；同键异正文/附件冲突
# --------------------------------------------------------------------------------------


def test_e01_concurrent_same_message_repost_creates_one_run(
    capacity_execution_stack: CapacityStack, tenant: TenantSeed, http: httpx.Client
) -> None:
    """两并发请求、PG、模型探针计数：同消息重投仅一个 Run；同 key 异正文/附件回冲突。

    断言位置（逐项）：
    ① 两个真实 Runtime 并发受理同一份请求：都拿到**同一个 run_id**；
    ② PG 里恰有一个 Run 与一条提交记录（不是"两个 Run 各自看起来成功"）；
    ③ 模型探针只收到一次真实调用（重投没有触发第二次执行）；
    ④ 同键换正文 → `IDEMPOTENCY_MISMATCH`；同键加附件 → 同样冲突；Run 数不增。
    """
    stack = capacity_execution_stack
    probe_control(http, stack.model_probe.url, "reset")
    pool = _seed_pool(stack, tenant)
    idempotency_key = f"capacity-idem-{uuid.uuid4().hex}"
    payload = build_run_payload(
        agent_id=pool.agent_id, user_id=tenant.user_id, text="same message", message_id="idem-message"
    )
    runtimes = [stack.url_of("agent-runtime", 0), stack.url_of("agent-runtime", 1)]

    def submit(runtime_url: str) -> Any:
        return post_run(
            http,
            runtime_url,
            tenant_id=tenant.tenant_id,
            agent_id=pool.agent_id,
            user_id=tenant.user_id,
            text="same message",
            idempotency_key=idempotency_key,
            payload=payload,
        )

    # ① 并发重投：两个 Runtime 同时受理同一份请求
    with ThreadPoolExecutor(max_workers=2) as pool_executor:
        outcomes = [future.result(timeout=60) for future in (
            pool_executor.submit(submit, runtimes[0]),
            pool_executor.submit(submit, runtimes[1]),
        )]
    assert [item.status_code for item in outcomes] == [200, 200], [item.body for item in outcomes]
    run_ids = {item.run_id for item in outcomes}
    assert len(run_ids) == 1 and None not in run_ids, [item.body for item in outcomes]
    run_id = next(iter(run_ids))
    assert wait_run_terminal(stack.database_url, tenant.tenant_id, run_id)["status"] == "COMPLETED"

    # ② PG：恰一个 Run + 一条提交记录（按本键/本 Run 精确计数，不受同租户其它用例影响）
    counts = fetch_one(
        stack.database_url,
        "SELECT (SELECT count(*) FROM runtime.run_record WHERE tenant_id = $1 AND id = $2) AS runs,"
        " (SELECT count(*) FROM runtime.run_submission"
        "  WHERE tenant_id = $1 AND idempotency_key = $3) AS submissions",
        tenant.tenant_id,
        run_id,
        idempotency_key,
    )
    assert counts == {"runs": 1, "submissions": 1}, counts

    # ③ 模型探针计数：重投没有带来第二次执行
    calls = [
        call
        for call in probe_state(http, stack.model_probe.url)["calls"]
        if call["path"] == "/v1/chat/completions"
    ]
    assert len(calls) == 1, calls

    # ④ 同键异正文 / 异附件 → IDEMPOTENCY_MISMATCH，且不产生第二个 Run
    different_text = post_run(
        http,
        runtimes[1],
        tenant_id=tenant.tenant_id,
        agent_id=pool.agent_id,
        user_id=tenant.user_id,
        text="different body",
        idempotency_key=idempotency_key,
        payload={**payload, "message": {**payload["message"], "text": "different body"}},
    )
    assert different_text.status_code == 409, different_text.body
    assert "IDEMPOTENCY_MISMATCH" in different_text.body, different_text.body
    attachment = {
        "storage_key": "capacity/never-fetched.txt",
        "kind": "DOCUMENT",
        "media_type": "text/plain",
        "size": 4,
        "checksum": "sha256:" + "0" * 64,
        "source_channel": "WEBCHAT",
    }
    different_attachment = post_run(
        http,
        runtimes[0],
        tenant_id=tenant.tenant_id,
        agent_id=pool.agent_id,
        user_id=tenant.user_id,
        text="same message",
        idempotency_key=idempotency_key,
        payload={
            **payload,
            "message": {**payload["message"], "type": "attachment", "attachments": [attachment]},
        },
    )
    assert different_attachment.status_code == 409, different_attachment.body
    assert "IDEMPOTENCY_MISMATCH" in different_attachment.body, different_attachment.body
    assert (
        fetch_one(
            stack.database_url,
            "SELECT count(*) AS runs FROM runtime.run_record WHERE tenant_id = $1",
            tenant.tenant_id,
        )["runs"]
        == 1
    )


# --------------------------------------------------------------------------------------
# E-15：外部写重试安全、结果待核实与旧租约
# --------------------------------------------------------------------------------------


def _submit_external_write(
    stack: CapacityStack,
    tenant: TenantSeed,
    http: httpx.Client,
    *,
    effect_probe: EffectProbe,
    retry_safety: str,
    lose_first_response: bool = True,
    block_sec: int = 0,
    pid_file: Path | None = None,
    max_attempts: int = 3,
) -> tuple[TaskFixture, dict[str, Any]]:
    """经真实 Worker 受理边界提交一个"外部写"Task；返回现场与受理回执。"""
    storage_key = f"skills/capacity-external-write-{uuid.uuid4().hex[:8]}/v1/skill.zip"
    checksum = write_skill_package(stack.artifacts_root, storage_key, EXTERNAL_WRITE_SCRIPT)
    skill_id, artifact_id = uuid.uuid4(), uuid.uuid4()
    body = build_task_body(
        tenant_id=tenant.tenant_id,
        agent_id=uuid.uuid4(),
        actor_user_id=tenant.user_id,
        skill_id=skill_id,
        artifact_id=artifact_id,
        storage_key=storage_key,
        checksum=checksum,
        idempotency_key=f"capacity-op-{uuid.uuid4().hex}",
        input_data={
            "probe_url": effect_probe.url,
            "op": "write",
            "lose_first_response": lose_first_response,
            "block_sec": block_sec,
            "pid_file": str(pid_file) if pid_file is not None else None,
        },
        retry_safety=retry_safety,
        max_attempts=max_attempts,
    )
    fixture = TaskFixture(
        body=body,
        idempotency_key=str(body["idempotency_key"]),
        skill_id=skill_id,
        artifact_id=artifact_id,
    )
    status, accepted = submit_task(
        http,
        stack.url_of("agent-worker", 0),
        headers=service_headers(tenant.tenant_id, stack.internal_token),
        body=fixture.body,
        idempotency_key=fixture.idempotency_key,
    )
    assert status == 200 and accepted.get("task_id"), (status, accepted)
    return fixture, accepted


def _wait_task(
    stack: CapacityStack, tenant: TenantSeed, task_id: str, predicate: Any, *, label: str
) -> dict[str, Any]:
    return wait_until(
        lambda: value
        if (value := task_row(stack.database_url, tenant.tenant_id, task_id)) and predicate(value)
        else None,
        timeout_sec=TASK_WAIT_SEC,
        interval_sec=0.25,
        label=label,
    )


def test_e15_lost_response_retry_reuses_business_key_and_applies_effect_once(
    capacity_execution_stack: CapacityStack,
    tenant: TenantSeed,
    http: httpx.Client,
    effect_probe: EffectProbe,
) -> None:
    """外部成功而应答丢失：幂等写复用业务键、外部效果恰一次。

    断言位置（逐项）：
    ① 第一次尝试真的产生了外部写（真实 Skill 子进程对真实 HTTP 服务写），随后进程崩溃；
    ② 冻结的 `retry_safety=IDEMPOTENT` 允许有界重试：Task 回到 QUEUED 并带 RETRY 事件；
    ③ 重试复用**同一个业务键**（`MUAD_TASK_IDEMPOTENCY_KEY` 在重试间恒定）并先核查已有结果；
    ④ 终态 COMPLETED，外部写仍只有一次，结果里 `reused=true`。
    """
    stack = capacity_execution_stack
    fixture, accepted = _submit_external_write(
        stack, tenant, http, effect_probe=effect_probe, retry_safety="IDEMPOTENT"
    )
    task_id = accepted["task_id"]

    # ① 第一次尝试的外部写已发生（远端成功），但应答丢失（skill 以非零码退出）
    first_attempt = _wait_task(
        stack,
        tenant,
        task_id,
        lambda row: row["attempt"] >= 1 and effect_probe.writes,
        label="first attempt external write",
    )
    assert first_attempt["attempt"] == 1, first_attempt
    assert effect_probe.writes[0] == {
        "business_key": fixture.idempotency_key,
        "attempt": 1,
    }, effect_probe.writes

    # ② 重试安全 → 有界重排（带 RETRY 事件），不是终态
    requeued = _wait_task(
        stack,
        tenant,
        task_id,
        lambda row: row["status"] == "QUEUED" and row["attempt"] == 1,
        label="requeue after lost response",
    )
    assert requeued["lease_owner"] is None, requeued
    assert "RETRY" in task_events(stack.database_url, tenant.tenant_id, task_id)

    # ③④ 重试复用业务键：外部写恰一次，结果标记 reused
    final = _wait_task(
        stack,
        tenant,
        task_id,
        lambda row: row["status"] in TERMINAL_TASK_STATUSES,
        label="task terminal",
    )
    assert final["status"] == "COMPLETED", final
    assert final["attempt"] == 2, final
    assert final["idempotency_key"] == fixture.idempotency_key, final
    assert len(effect_probe.writes) == 1, effect_probe.writes
    assert [row["business_key"] for row in effect_probe.reads] == [fixture.idempotency_key] * 2
    assert final["result_json"]["reused"] is True, final["result_json"]
    assert final["result_json"]["business_key"] == fixture.idempotency_key, final["result_json"]
    events = task_events(stack.database_url, tenant.tenant_id, task_id)
    assert events[0] == "CREATED" and events[-1] == "COMPLETED", events
    assert events.count("CLAIMED") == 2 and "RETRY" in events, events


@pytest.mark.parametrize("retry_safety", ["NON_RETRYABLE", ""])
def test_e15_non_idempotent_write_is_not_redone_and_stays_unverified(
    capacity_execution_stack: CapacityStack,
    tenant: TenantSeed,
    http: httpx.Client,
    effect_probe: EffectProbe,
    retry_safety: str,
) -> None:
    """非幂等（或未声明安全性）的外部写在应答丢失后不自动重做，显示结果待核实。

    断言位置（逐项）：
    ① 外部写已经发生（恰一次），Task 落 `FAILED(TASK_RESULT_UNVERIFIED)`；
    ② `attempt` 不推进（没有自动重做），事件里没有 RETRY，也没有把待核实当成功；
    ③ 同键重投只回放首次受理结果（同一 task_id），不产生第二次外部效果。
    """
    stack = capacity_execution_stack
    fixture, accepted = _submit_external_write(
        stack, tenant, http, effect_probe=effect_probe, retry_safety=retry_safety
    )
    task_id = accepted["task_id"]

    # ① 外部写恰一次，终态待核实
    final = _wait_task(
        stack,
        tenant,
        task_id,
        lambda row: row["status"] in TERMINAL_TASK_STATUSES,
        label="unverified terminal",
    )
    assert final["status"] == "FAILED", final
    assert final["error_code"] == "TASK_RESULT_UNVERIFIED", final
    assert len(effect_probe.writes) == 1, effect_probe.writes
    assert [row["attempt"] for row in effect_probe.reads] == [1], effect_probe.reads

    # ② 不自动重做：attempt 不推进、没有 RETRY 事件、结果为空
    assert final["attempt"] == 1, final
    assert final["result_json"] is None, final
    events = task_events(stack.database_url, tenant.tenant_id, task_id)
    assert events == ["CREATED", "CLAIMED", "FAILED"], events

    # ③ 同键重投复用业务键：同一 task_id，不新增外部效果
    replay_status, replayed = submit_task(
        http,
        stack.url_of("agent-worker", 1),
        headers=service_headers(tenant.tenant_id, stack.internal_token),
        body=fixture.body,
        idempotency_key=fixture.idempotency_key,
    )
    assert replay_status == 200, (replay_status, replayed)
    assert replayed["task_id"] == task_id, replayed
    assert len(effect_probe.writes) == 1, effect_probe.writes
    assert (
        fetch_one(
            stack.database_url,
            "SELECT count(*) AS tasks FROM task.task_execution"
            " WHERE tenant_id = $1 AND idempotency_key = $2",
            tenant.tenant_id,
            fixture.idempotency_key,
        )["tasks"]
        == 1
    ), "重投不得新建 Task（业务键唯一）"


def test_e15_stale_lease_is_not_resubmitted_or_read_as_success(
    capacity_execution_stack: CapacityStack,
    tenant: TenantSeed,
    http: httpx.Client,
    effect_probe: EffectProbe,
    tmp_path: Path,
) -> None:
    """Worker 崩溃后：旧租约不被重排/重做，也不被自动解释成成功（真实 claim/reclaim）。

    断言位置（逐项）：
    ① 真实 Skill 子进程已经在受控外部服务上写入效果，Worker 进程组被 SIGKILL（模拟 Pod 崩溃）；
    ② PG 回收（另一 Worker 的维护循环）按冻结语义裁决：`FAILED(TASK_RESULT_UNVERIFIED)`，
       `attempt` 仍为 1，事件里没有 RETRY/COMPLETED；
    ③ 外部效果恰一次，且旧持有者无法再提交（同键重投仍是同一 task_id、同一待核实结局）。
    """
    stack = capacity_execution_stack
    pid_file = tmp_path / "capacity-skill.pid"
    fixture, accepted = _submit_external_write(
        stack,
        tenant,
        http,
        effect_probe=effect_probe,
        retry_safety="NON_RETRYABLE",
        lose_first_response=False,
        block_sec=120,
        pid_file=pid_file,
    )
    task_id = accepted["task_id"]
    # ① 真实执行中：外部写已发生，Skill 子进程在跑
    running = _wait_task(
        stack,
        tenant,
        task_id,
        lambda row: row["status"] == "RUNNING" and effect_probe.writes and pid_file.exists(),
        label="running skill with external effect",
    )
    assert running["attempt"] == 1, running
    assert len(effect_probe.writes) == 1, effect_probe.writes
    skill_pid = int(pid_file.read_text())
    lease_owner = str(running["lease_owner"])
    crashed = next(
        instance
        for instance in stack.instances("agent-worker")
        if str(instance.pid) == lease_owner.rsplit(":", 1)[-1]
    )
    surviving = next(
        instance for instance in stack.instances("agent-worker") if instance is not crashed
    )
    stack.kill_instance(crashed)
    assert crashed.alive is False, "崩溃的 Worker 进程组必须真的消失"
    # Skill 子进程以独立会话运行（执行器的进程组语义）：本地栈里它是 Worker 的孤儿，
    # 用例按 Pod 崩溃的语义显式收尾并断言没有残留进程。
    try:
        os.killpg(skill_pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _assert_pid_gone(skill_pid)

    # ② 另一个 Worker 的回收按冻结语义裁决：待核实，不重排、不解释成成功
    final = _wait_task(
        stack,
        tenant,
        task_id,
        lambda row: row["status"] in TERMINAL_TASK_STATUSES,
        label="reclaim terminal",
    )
    assert final["status"] == "FAILED", final
    assert final["error_code"] == "TASK_RESULT_UNVERIFIED", final
    assert final["attempt"] == 1, final
    assert final["lease_owner"] is None, final
    events = task_events(stack.database_url, tenant.tenant_id, task_id)
    assert events == ["CREATED", "CLAIMED", "FAILED"], events

    # ③ 外部效果恰一次；旧租约的同键重投仍是同一 task_id 与同一结局
    assert len(effect_probe.writes) == 1, effect_probe.writes
    replay_status, replayed = submit_task(
        http,
        surviving.url,
        headers=service_headers(tenant.tenant_id, stack.internal_token),
        body=fixture.body,
        idempotency_key=fixture.idempotency_key,
    )
    assert replay_status == 200 and replayed["task_id"] == task_id, (replay_status, replayed)
    assert _wait_task(
        stack,
        tenant,
        task_id,
        lambda row: row["status"] == "FAILED" and row["error_code"] == "TASK_RESULT_UNVERIFIED",
        label="still unverified after replay",
    )["attempt"] == 1
    assert len(effect_probe.writes) == 1, effect_probe.writes


def _assert_pid_gone(pid: int) -> None:
    state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
    assert state.stdout.strip() == "", f"skill 子进程 {pid} 仍在：{state.stdout.strip()}"
