"""B-234：[integration] 外部副作用重试安全与结果待核实（design §3.6 / §3.7）。

真实边界（不 mock 存储或租约）：
- 真实 `WorkerLoop`/`TaskClaimer`/任务槽监督业务路径：claim、执行、心跳、收尾、回收全走真实代码；
- 隔离 PostgreSQL（空库 + `alembic upgrade head`，跑完即 drop）：Task 行/事件/租约只落在本次
  独立库，不触碰共享 dev 库；
- 真实子进程：`SkillTaskExecutor` + `ScriptSkillExecutor` 拉起真实 Skill 子进程，skill 脚本用
  `urllib` 对受控外部写 HTTP 服务发起真实 socket 请求（本文件内的 `_EffectProbe` 真实 HTTP
  服务器），外部效果与业务键都由探针在真实协议层记录；
- 真实 PG 行事实驱动心跳/回收：`_renew_lease`/`reclaim_expired`，不替换存储或租约。

关键断言（B-234 契约）：
1. READ_ONLY/IDEMPOTENT/NON_RETRYABLE 由**冻结的可信配置**（execution snapshot 的
   `budget.retry_safety`）驱动：同一「外部效果已发生、应答丢失」的失败形状，按冻结值分别
   得到「有界重试」「有界重试且复用业务键」「结果待核实不自动重做」；
2. 远端成功应答丢失后，幂等重试复用**原业务键**（`task.idempotency_key` 在重试间恒定），
   重试先核查已有外部结果，外部效果恰一次；
3. 未知/未声明安全性或 MCP `readOnlyHint` 单独出现，一律不得授权重试：非幂等效果不自动
   重做，落 `FAILED(TASK_RESULT_UNVERIFIED)` 待核实；
4. 与 TASK-033 的回收衔接：失约（心跳死亡）叫停本地执行后仍按同一结局语义收尾——非幂等
   效果不自动重做、终态待核实；崩溃回收（过期租约）同样不把「待核实」重排成自动重做，
   重试安全的任务保持 TASK-033 的退避重排语义。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import threading
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from muad_agent_worker.infrastructure import db as worker_db
from muad_agent_worker.infrastructure.db import get_session_factory
from muad_agent_worker.infrastructure.models.task import TaskEvent, TaskExecution
from muad_agent_worker.worker import service as worker_service
from muad_agent_worker.worker.execution_outcomes import TASK_RESULT_UNVERIFIED
from muad_agent_worker.worker.executor import SkillTaskExecutor
from muad_agent_worker.worker.service import WorkerLoop
from muad_common import SharedSettings
from muad_contracts import ToolRetrySafety
from muad_contracts.tool_runtime import frozen_retry_safety, task_retry_safety
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

INSTANCE = "worker-retry-safety-b234"
WAIT_TIMEOUT_SEC = 20.0
SNAPSHOT_HASH = "sha256:" + "c" * 64
TERMINAL_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")


# --------------------------------------------------------------------------- #
# 隔离数据存储：本任务边界
# --------------------------------------------------------------------------- #


@pytest.fixture
async def isolated_worker(
    async_tool_datastore: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """隔离 PostgreSQL/Redis 号位 + Worker 进程环境；跑完 drop，不触碰共享 dev 库。"""
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "retry-safety-internal-token")
    await worker_db.dispose_engine()
    try:
        yield get_session_factory()
    finally:
        await worker_db.dispose_engine()


# --------------------------------------------------------------------------- #
# 受控外部写 HTTP 服务：真实 socket 协议探针
# --------------------------------------------------------------------------- #


class _EffectProbe:
    """记录外部写（POST /effects）与崩溃后核查（GET /effects）的真实 HTTP 服务。"""

    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []
        self.reads: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        probe = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                with probe._lock:
                    probe.writes.append(dict(payload))
                self._respond({"ok": True})

            def do_GET(self) -> None:
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

            def _respond(self, payload: dict[str, Any]) -> None:
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


@pytest.fixture
def effect_probe() -> Iterator[_EffectProbe]:
    probe = _EffectProbe()
    try:
        yield probe
    finally:
        probe.close()


# --------------------------------------------------------------------------- #
# 真实 Skill 子进程现场：效果先行、应答丢失
# --------------------------------------------------------------------------- #

SKILL_SCRIPT = """
import json
import os
import sys
import time
import urllib.request


def _request(method, url, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
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


class _StaticArtifactResolver:
    """Skill 制品缓存接缝：返回测试现场的真实脚本目录（进程边界仍是真实子进程）。"""

    def __init__(self, ready_dir: Path) -> None:
        self._ready_dir = ready_dir

    async def ensure(self, *, artifact_id: str, storage_key: str, checksum: str) -> Path:
        return self._ready_dir


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "main.py").write_text(SKILL_SCRIPT, encoding="utf-8")
    return tmp_path


def _settings(**overrides: Any) -> SharedSettings:
    values: dict[str, Any] = {
        "worker_execution_concurrency": 2,
        "task_heartbeat_sec": 1,
        "task_cancel_check_sec": 1,
        "worker_poll_interval_sec": 1,
    }
    values.update(overrides)
    return SharedSettings(**values)


def _worker(
    factory: async_sessionmaker[AsyncSession], skill_dir: Path, settings: SharedSettings | None = None
) -> WorkerLoop:
    return WorkerLoop(
        factory,
        settings or _settings(),
        executor=SkillTaskExecutor(_StaticArtifactResolver(skill_dir)),
        instance_id=INSTANCE,
    )


async def _seed_task(
    factory: async_sessionmaker[AsyncSession],
    *,
    budget: dict[str, Any],
    probe_url: str,
    op: str,
    lose_first_response: bool = True,
    block_sec: int = 0,
    pid_file: Path | None = None,
    **overrides: Any,
) -> TaskExecution:
    moment = datetime.now(UTC)
    task_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    values: dict[str, Any] = {
        "id": task_id,
        "tenant_id": f"retry-safety-{uuid.uuid4().hex[:12]}",
        "agent_id": uuid.uuid4(),
        "actor_user_id": uuid.uuid4(),
        "intent_key": "retry_safety_probe",
        "skill_id": uuid.uuid4(),
        "skill_artifact_id": artifact_id,
        "trigger_type": "IMMEDIATE",
        "execution_mode": "ASYNC",
        "task_type": "SKILL",
        "status": "QUEUED",
        "input_json": {
            "probe_url": probe_url,
            "op": op,
            "lose_first_response": lose_first_response,
            "block_sec": block_sec,
            "pid_file": str(pid_file) if pid_file is not None else None,
        },
        "execution_snapshot_schema_version": 1,
        "execution_snapshot_json": {
            "schema_version": 1,
            "skills": [
                {
                    "artifact_id": str(artifact_id),
                    "storage_key": "skills/probe.zip",
                    "checksum": "sha256:" + "a" * 64,
                }
            ],
            "budget": dict(budget),
        },
        "snapshot_hash": SNAPSHOT_HASH,
        "idempotency_key": f"retry-safety-{uuid.uuid4()}",
        "priority": 100,
        "attempt": 0,
        "max_attempts": 3,
        "not_before": moment,
        "deadline_at": moment + timedelta(hours=1),
        "delivery_mode": "FINAL_ONLY",
        "delivery_status": "PENDING",
        "delivery_key": f"task:{task_id}:final",
        "delivery_attempts": 0,
    }
    values.update(overrides)
    async with factory() as session, session.begin():
        task = TaskExecution(**values)
        session.add(task)
        await session.flush()
    return task


@dataclass(frozen=True, slots=True)
class _TaskState:
    status: str
    attempt: int
    error_code: str | None
    result: dict[str, Any] | None
    not_before: datetime
    lease_owner: str | None


async def _state(factory: async_sessionmaker[AsyncSession], task_id: uuid.UUID) -> _TaskState:
    async with factory() as session:
        row = await session.get(TaskExecution, task_id)
        assert row is not None, f"task {task_id} must exist"
        return _TaskState(
            status=str(row.status),
            attempt=int(row.attempt),
            error_code=row.error_code,
            result=dict(row.result_json) if row.result_json else None,
            not_before=row.not_before,
            lease_owner=row.lease_owner,
        )


async def _event_types(factory: async_sessionmaker[AsyncSession], task_id: uuid.UUID) -> list[str]:
    async with factory() as session:
        rows = await session.scalars(
            select(TaskEvent.event_type)
            .where(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.seq.asc())
        )
    return list(rows)


async def _wait_for(predicate: Callable[[], bool], *, timeout: float = WAIT_TIMEOUT_SEC) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def _wait_state(
    factory: async_sessionmaker[AsyncSession],
    task_id: uuid.UUID,
    predicate: Callable[[_TaskState], bool],
    *,
    timeout: float = WAIT_TIMEOUT_SEC,
) -> _TaskState:
    last: _TaskState | None = None
    try:
        async with asyncio.timeout(timeout):
            while True:
                last = await _state(factory, task_id)
                if predicate(last):
                    return last
                await asyncio.sleep(0.05)
    except TimeoutError as exc:
        raise AssertionError(f"task {task_id} never matched; last state={last}") from exc


def _process_alive(pid: int) -> bool:
    state = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


# --------------------------------------------------------------------------- #
# 1. 幂等：应答丢失后重试复用业务键，外部效果恰一次
# --------------------------------------------------------------------------- #


@pytest.mark.integration
async def test_b234_idempotent_retry_reuses_business_key_and_keeps_effect_once(
    isolated_worker: async_sessionmaker[AsyncSession], skill_dir: Path, effect_probe: _EffectProbe
) -> None:
    factory = isolated_worker
    task = await _seed_task(
        factory,
        budget={"retry_safety": "IDEMPOTENT"},
        probe_url=effect_probe.url,
        op="write",
    )
    worker = _worker(factory, skill_dir)
    try:
        assert await worker.run_once() == task.id
        first = await _state(factory, task.id)
        assert first.status == "QUEUED", "幂等操作可有界重试，不能被当成不可重试"
        assert first.attempt == 1
        assert len(effect_probe.writes) == 1, "第一次尝试已经产生外部效果（远端成功、应答丢失）"
        assert effect_probe.writes[0] == {
            "business_key": task.idempotency_key,
            "attempt": 1,
        }
        # 退避后重试：同一业务键先核查已有外部结果，不重复应用
        assert await worker.run_once(now=first.not_before + timedelta(seconds=1)) == task.id
        final = await _state(factory, task.id)
        assert final.status == "COMPLETED"
        assert final.attempt == 2
        assert len(effect_probe.writes) == 1, "幂等重试复用了业务键，外部效果必须恰一次"
        assert final.result is not None
        assert final.result["business_key"] == task.idempotency_key, "重试必须复用原业务键"
        assert final.result["reused"] is True, "重试先核查了已存在的外部结果"
        assert [row["business_key"] for row in effect_probe.reads] == [task.idempotency_key] * 2
    finally:
        await worker.aclose()


# --------------------------------------------------------------------------- #
# 2. 只读：失败仍可有界重试
# --------------------------------------------------------------------------- #


@pytest.mark.integration
async def test_b234_read_only_failure_retries_within_the_budget(
    isolated_worker: async_sessionmaker[AsyncSession], skill_dir: Path, effect_probe: _EffectProbe
) -> None:
    factory = isolated_worker
    task = await _seed_task(
        factory,
        budget={"retry_safety": "READ_ONLY"},
        probe_url=effect_probe.url,
        op="read",
    )
    worker = _worker(factory, skill_dir)
    try:
        assert await worker.run_once() == task.id
        first = await _state(factory, task.id)
        assert (first.status, first.attempt) == ("QUEUED", 1)
        assert len(effect_probe.reads) == 1
        assert effect_probe.writes == [], "只读操作不得产生外部写"
        assert await worker.run_once(now=first.not_before + timedelta(seconds=1)) == task.id
        final = await _state(factory, task.id)
        assert (final.status, final.attempt) == ("COMPLETED", 2)
        assert [row["attempt"] for row in effect_probe.reads] == [1, 2], "只读操作仍可有界重试"
        assert effect_probe.writes == []
    finally:
        await worker.aclose()


# --------------------------------------------------------------------------- #
# 3. 非幂等/未知/仅提示：不自动重做，落结果待核实
# --------------------------------------------------------------------------- #


@pytest.mark.integration
@pytest.mark.parametrize(
    "budget",
    [
        pytest.param({"retry_safety": "NON_RETRYABLE"}, id="declared-non-retryable"),
        pytest.param({}, id="undeclared"),
        pytest.param({"retry_safety": "TOTALLY_SAFE"}, id="unknown-value"),
        pytest.param({"readOnlyHint": True}, id="mcp-read-only-hint-only"),
    ],
)
async def test_b234_non_retryable_effect_is_not_redone_and_stays_unverified(
    isolated_worker: async_sessionmaker[AsyncSession],
    skill_dir: Path,
    effect_probe: _EffectProbe,
    budget: dict[str, Any],
) -> None:
    factory = isolated_worker
    task = await _seed_task(
        factory, budget=budget, probe_url=effect_probe.url, op="write"
    )
    worker = _worker(factory, skill_dir)
    try:
        assert await worker.run_once() == task.id
        state = await _state(factory, task.id)
        assert state.status == "FAILED"
        assert state.error_code == TASK_RESULT_UNVERIFIED, "无法确认远端结果 → 待核实，不是普通失败码"
        assert state.attempt == 1, "不得自动重做：attempt 不推进"
        assert state.result is None
        assert state.lease_owner is None
        assert len(effect_probe.writes) == 1, "不得假定外部副作用未发生而重复执行"
        assert [row["attempt"] for row in effect_probe.reads] == [1]
        assert await _event_types(factory, task.id) == ["CLAIMED", "FAILED"], "没有 RETRY 事件"
        # 与槽回收衔接：终态待核实不被回收重排（不改变结局语义）
        assert await worker.reclaim_expired() == 0
        assert (await _state(factory, task.id)).status == "FAILED"
        assert await _event_types(factory, task.id) == ["CLAIMED", "FAILED"]
    finally:
        await worker.aclose()


def test_b234_frozen_retry_safety_reads_only_the_trusted_key() -> None:
    """冻结解析只认可信键：未知/未声明/非法取值 → NON_RETRYABLE，提示字段不参与。"""
    assert frozen_retry_safety({}) is ToolRetrySafety.NON_RETRYABLE
    assert frozen_retry_safety({"retry_safety": "READ_ONLY"}) is ToolRetrySafety.READ_ONLY
    assert frozen_retry_safety({"retry_safety": "IDEMPOTENT"}) is ToolRetrySafety.IDEMPOTENT
    assert frozen_retry_safety({"retry_safety": "MAYBE"}) is ToolRetrySafety.NON_RETRYABLE
    assert frozen_retry_safety({"retry_safety": 123}) is ToolRetrySafety.NON_RETRYABLE
    # MCP readOnlyHint 单独不能授权重试；也不能覆盖可信声明
    assert frozen_retry_safety({"readOnlyHint": True}) is ToolRetrySafety.NON_RETRYABLE
    assert frozen_retry_safety({"annotations": {"readOnlyHint": True}}) is ToolRetrySafety.NON_RETRYABLE
    assert (
        frozen_retry_safety({"retry_safety": "NON_RETRYABLE", "readOnlyHint": True})
        is ToolRetrySafety.NON_RETRYABLE
    )
    assert task_retry_safety({"budget": {"retry_safety": "IDEMPOTENT"}}) is ToolRetrySafety.IDEMPOTENT
    assert task_retry_safety({"budget": {"readOnlyHint": True}}) is ToolRetrySafety.NON_RETRYABLE
    assert task_retry_safety({}) is ToolRetrySafety.NON_RETRYABLE


# --------------------------------------------------------------------------- #
# 4. 与 TASK-033 回收衔接：回收不改变结局语义
# --------------------------------------------------------------------------- #


@pytest.mark.integration
async def test_b234_crash_reclaim_requeues_only_retry_safe_tasks(
    isolated_worker: async_sessionmaker[AsyncSession],
) -> None:
    """崩溃回收按同一冻结语义分流：非重试安全不重排（待核实），重试安全保持退避重排。"""
    factory = isolated_worker
    now = datetime.now(UTC)
    crashed_at = now - timedelta(minutes=1)
    unsafe = await _seed_task(
        factory,
        budget={"retry_safety": "NON_RETRYABLE"},
        probe_url="http://127.0.0.1:1",
        op="write",
        status="RUNNING",
        attempt=1,
        lease_owner="dead-worker",
        lease_until=crashed_at,
    )
    safe = await _seed_task(
        factory,
        budget={"retry_safety": "IDEMPOTENT"},
        probe_url="http://127.0.0.1:1",
        op="write",
        status="RUNNING",
        attempt=1,
        lease_owner="dead-worker",
        lease_until=crashed_at,
    )
    batch_parent = await _seed_task(
        factory,
        budget={},
        probe_url="http://127.0.0.1:1",
        op="write",
        status="RUNNING",
        attempt=1,
        task_type="BATCH",
        lease_owner="dead-worker",
        lease_until=crashed_at,
    )
    worker = WorkerLoop(factory, _settings(), instance_id=INSTANCE)
    try:
        assert await worker.reclaim_expired(now=now) == 2, "重试安全与幂等停放的崩溃任务回到队列"
        unsafe_state = await _state(factory, unsafe.id)
        assert unsafe_state.status == "FAILED"
        assert unsafe_state.error_code == TASK_RESULT_UNVERIFIED, "回收不把待核实重排成自动重做"
        assert unsafe_state.attempt == 1
        assert await _event_types(factory, unsafe.id) == ["FAILED"]
        safe_state = await _state(factory, safe.id)
        assert safe_state.status == "QUEUED", "重试安全的任务保持 TASK-033 的退避重排语义"
        assert safe_state.attempt == 1
        assert await _event_types(factory, safe.id) == ["RECLAIMED"]
        # 批量父任务是幂等停放（重排不重跑 Skill、扇出按唯一键去重），不按外部副作用拦截
        parent_state = await _state(factory, batch_parent.id)
        assert parent_state.status == "QUEUED"
        assert await _event_types(factory, batch_parent.id) == ["RECLAIMED"]
        assert await worker.reclaim_expired(now=now) == 0, "待核实是终态，不再被回收"
    finally:
        await worker.aclose()


@pytest.mark.integration
async def test_b234_lost_lease_keeps_unverified_outcome_and_is_not_requeued(
    isolated_worker: async_sessionmaker[AsyncSession],
    skill_dir: Path,
    effect_probe: _EffectProbe,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = isolated_worker
    pid_file = tmp_path / "skill.pid"
    task = await _seed_task(
        factory,
        budget={"retry_safety": "NON_RETRYABLE"},
        probe_url=effect_probe.url,
        op="write",
        lose_first_response=False,
        block_sec=60,
        pid_file=pid_file,
    )
    monkeypatch.setattr(worker_service, "HEARTBEAT_RENEW_RETRY_SEC", 0.01)
    worker = _worker(factory, skill_dir)
    try:
        assert await worker.run_pending() == 1
        await _wait_for(lambda: len(effect_probe.writes) == 1)
        assert _process_alive(int(pid_file.read_text()))

        async def failing_renew(task_id: uuid.UUID) -> tuple[bool, bool]:
            raise RuntimeError("database is down")

        monkeypatch.setattr(worker, "_renew_lease", failing_renew)
        state = await _wait_state(factory, task.id, lambda item: item.status in TERMINAL_STATUSES)
        assert state.status == "FAILED"
        assert state.error_code == TASK_RESULT_UNVERIFIED, "失约叫停后仍按同一结局语义收尾"
        assert state.attempt == 1
        assert len(effect_probe.writes) == 1, "失约不得自动重做非幂等效果"
        await _wait_for(lambda: not _process_alive(int(pid_file.read_text())))
        await _wait_for(lambda: worker.execution_count == 0)
        # 回收衔接：终态待核实不重排、不重跑；旧持有者也不能把它解释成成功
        assert await worker.reclaim_expired() == 0
        assert (await _state(factory, task.id)).status == "FAILED"
        assert await _event_types(factory, task.id) == ["CLAIMED", "FAILED"]
    finally:
        if pid_file.exists():
            with contextlib.suppress(ProcessLookupError, ValueError):
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
        await worker.aclose()
