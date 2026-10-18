"""B-295：[integration] Worker 装配模型外部调用预算与独立维护循环（design §3.2/§3.7/§4.1；TASK-095）。

真实边界（不 mock 存储、租约与外部协议）：

- **真实 Worker 装配根**：`muad_agent_worker.main.lifespan`（真实 `validate_startup`、真实 PG/Redis
  客户端、真实 `WorkerLoop`/`SchedulerLoop`/`DeliveryLoop`/`ResultDispatcher` 与维护循环）；
- **隔离 PostgreSQL + 隔离 Redis 号位**（`async_tool_datastore`：每用例一个空库 + `alembic upgrade
  head`，跑完 drop）：Task 行/租约/事件只落本次独立库，不触碰共享 dev 库、不做 DDL；
- **真实子进程**：Skill 经真实 `SkillArtifactCache`（隔离 artifact root 的 zip + checksum 校验 +
  解包）拉起真实 Python 子进程（退出用例里还有孙进程）；
- **真实 HTTP 协议探针**：真实 uvicorn TCP 服务承载 Console 平台设置端点；真实 TCP 监听（接受连接
  后**永不回应**）承载 Gateway 投递，让投递循环阻塞在真实 HTTP 读上。

关键断言（B-295 契约）：

1. **分池预算由装配根注入、唯一来源是启动设置**：control（Console 设置快照/定时解析）与 egress
   （Gateway 投递、Runtime 结果发件/收件箱/产物解析）各自建池；池大小与连接/池/读超时随启动设置
   变化，装配根不复制默认值；本进程**不建它不调用的池**（MCP/上传/流/模型）——design §3.7
   「实例只创建实际需要的池」；
2. **并发与期限**：任务槽上限取 `WORKER_EXECUTION_CONCURRENCY`；结果发件策略取启动设置且
   lease > timeout；读超时按调用类别；
3. **续租/回收/领取/投递/调度互不阻塞**：五条循环是各自独立的 `asyncio` 任务；投递循环阻塞在真实
   HTTP 读上时，领取循环照常 claim 并执行真实 Skill、心跳照常续租、维护循环照常回收过期租约、
   调度循环照常 sweep 过期 deadline；
4. **退出清子进程组**：lifespan 退出（进程退出路径）取消领取循环 → 关闭任务槽 → 执行器收尾整组
   子进程，且失约实例不写任何伪造结论；
5. **Task 旧 policy 不补默认**：冻结快照没有 `budget.retry_safety` 的旧行不被补新默认（快照逐字节
   不变、未知安全性不自动重做），而声明了 IDEMPOTENT 的行仍按**自己的**冻结值有界重试。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import os
import signal
import socket
import threading
import time
import uuid
import zipfile
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from muad_agent_worker.application.delivery_routes import upsert_delivery_route
from muad_agent_worker.infrastructure import db as worker_db
from muad_agent_worker.infrastructure.db import get_session_factory
from muad_agent_worker.infrastructure.http_clients import (
    WORKER_HTTP_POOLS,
    WorkerHttpPools,
    pool_limits,
    pool_timeout,
)
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.execution_outcomes import TASK_RESULT_UNVERIFIED
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_common import SharedSettings
from muad_contracts import DeliveryRouteInput
from muad_contracts.platform_settings import parse_platform_settings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration

SNAPSHOT_HASH = "sha256:" + "d" * 64
PLATFORM_SETTINGS_PATH = "/internal/v1/platform-settings"
DEAD_GATEWAY_URL = "http://127.0.0.1:9"
WAIT_TIMEOUT_SEC = 25.0
LOOP_NAMES = (
    "worker.claim",
    "worker.maintenance",
    "worker.scheduler",
    "worker.delivery",
    "worker.results",
)


# --------------------------------------------------------------------------------------
# 隔离数据存储 + 真实 Skill 制品缓存 + 真实 HTTP 探针
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IsolatedWorker:
    factory: async_sessionmaker[AsyncSession]
    tmp_path: Path


@pytest.fixture
async def isolated_worker(
    async_tool_datastore: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[IsolatedWorker]:
    """隔离 PostgreSQL/Redis 号位 + Worker 进程环境；跑完 drop，不触碰共享 dev 库。"""
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("SKILL_CACHE_ROOT", str(tmp_path / "skill-cache"))
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "b295-internal-token")
    await worker_db.dispose_engine()
    try:
        yield IsolatedWorker(factory=get_session_factory(), tmp_path=tmp_path)
    finally:
        # 退出时清缓存：后续套件按已还原的环境重建 engine，不指向已 drop 的库。
        await worker_db.dispose_engine()


@pytest.fixture
def real_skill_cache(
    isolated_worker: IsolatedWorker, monkeypatch: pytest.MonkeyPatch
) -> SkillArtifactCache:
    """装配根惰性执行器消费的真实制品缓存：只把**模块级单例**指向本次隔离 root。

    换成的是同一个真实类（`SkillArtifactCache` + `NfsArtifactStore`）：zip 落盘、checksum 校验、
    安全解包、READY 落盘全部走生产路径，不替身任何行为。
    """
    from muad_agent_worker.bootstrap import artifacts

    cache = SkillArtifactCache(
        NfsArtifactStore(isolated_worker.tmp_path), isolated_worker.tmp_path / "skill-cache"
    )
    monkeypatch.setattr(artifacts, "skill_artifact_cache", cache)
    return cache


class _ProbeServer:
    """真实 uvicorn TCP 服务（127.0.0.1:0）：外部 HTTP 协议探针的宿主。"""

    def __init__(self, app: FastAPI) -> None:
        self._server = uvicorn.Server(
            uvicorn.Config(app=app, host="127.0.0.1", port=0, log_level="error")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> str:
        self._thread.start()
        deadline = time.monotonic() + 15
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not self._server.started:
            raise RuntimeError("probe server failed to start")
        port = self._server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


@pytest.fixture
def settings_probe() -> Iterator[str]:
    """真实 Console 平台设置端点：投递/创建边界的设置快照经真实 HTTP 取。"""
    app = FastAPI()

    @app.get(PLATFORM_SETTINGS_PATH)
    async def platform_settings() -> dict[str, Any]:
        return {
            "code": "0",
            "data": {"revision": 1, "settings": asdict(parse_platform_settings({}))},
        }

    server = _ProbeServer(app)
    url = server.start()
    try:
        yield url
    finally:
        server.stop()


class _HangingGateway:
    """真实 TCP 监听：接受连接后**永不回应**，投递循环于是阻塞在真实 HTTP 读上。"""

    def __init__(self) -> None:
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self._accepted: list[socket.socket] = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._accept_forever, daemon=True)
        self._thread.start()

    def _accept_forever(self) -> None:
        while True:
            try:
                connection, _ = self._listener.accept()
            except OSError:
                return
            with self._lock:
                self._accepted.append(connection)

    @property
    def url(self) -> str:
        host, port = self._listener.getsockname()
        return f"http://{host}:{port}"

    @property
    def accepted(self) -> int:
        with self._lock:
            return len(self._accepted)

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._listener.close()
        with self._lock:
            for connection in self._accepted:
                with contextlib.suppress(OSError):
                    connection.close()


@pytest.fixture
def hanging_gateway() -> Iterator[_HangingGateway]:
    gateway = _HangingGateway()
    try:
        yield gateway
    finally:
        gateway.close()


# --------------------------------------------------------------------------------------
# 真实来源：可领取 Task 行 + 现场 Skill 包（zip 走真实制品库）
# --------------------------------------------------------------------------------------


def _publish_skill(tmp_path: Path, *, body: str) -> tuple[str, str]:
    """把真实 Skill 包写进隔离 artifact root（`NfsArtifactStore.write`），返回键与校验和。"""
    storage_key = f"skills/b295/{uuid.uuid4().hex}.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("scripts/main.py", body)
    payload = buffer.getvalue()
    NfsArtifactStore(tmp_path).write(storage_key, payload)
    return storage_key, "sha256:" + hashlib.sha256(payload).hexdigest()


def _snapshot(
    skill_artifact_id: uuid.UUID, storage_key: str, checksum: str, *, retry_safety: str | None
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "skills": [
            {
                "artifact_id": str(skill_artifact_id),
                "storage_key": storage_key,
                "checksum": checksum,
            }
        ],
    }
    if retry_safety is not None:
        snapshot["budget"] = {"retry_safety": retry_safety}
    return snapshot


async def _seed_task(
    factory: async_sessionmaker[AsyncSession],
    *,
    snapshot: dict[str, Any],
    priority: int = 100,
    **overrides: Any,
) -> TaskExecution:
    moment = datetime.now(UTC)
    task_id = uuid.uuid4()
    values: dict[str, Any] = {
        "id": task_id,
        "tenant_id": f"b295-{uuid.uuid4().hex[:12]}",
        "agent_id": uuid.uuid4(),
        "actor_user_id": uuid.uuid4(),
        "intent_key": "b295_probe",
        "skill_id": uuid.uuid4(),
        "skill_artifact_id": uuid.uuid4(),
        "trigger_type": "IMMEDIATE",
        "execution_mode": "ASYNC",
        "task_type": "SKILL",
        "status": "QUEUED",
        "input_json": {"probe": True},
        "execution_snapshot_schema_version": 1,
        "execution_snapshot_json": snapshot,
        "snapshot_hash": SNAPSHOT_HASH,
        "idempotency_key": f"b295-{uuid.uuid4()}",
        "priority": priority,
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


async def _seed_skill_task(
    factory: async_sessionmaker[AsyncSession],
    *,
    storage_key: str,
    checksum: str,
    retry_safety: str | None = "IDEMPOTENT",
    **overrides: Any,
) -> TaskExecution:
    """种一条指向真实 Skill 包的 Task：快照里的 artifact_id 与行的 `skill_artifact_id` 一致。"""
    artifact_id = uuid.uuid4()
    return await _seed_task(
        factory,
        snapshot=_snapshot(artifact_id, storage_key, checksum, retry_safety=retry_safety),
        skill_artifact_id=artifact_id,
        **overrides,
    )


async def _attach_wecom_route(factory: async_sessionmaker[AsyncSession], task: TaskExecution) -> None:
    """给行挂一条真实 WECOM 投递路由：投递循环会走 Gateway 出站（不是 Runtime 收件箱）。"""
    async with factory() as session, session.begin():
        route_id = await upsert_delivery_route(
            session,
            tenant_id=task.tenant_id,
            platform_user_id=task.actor_user_id,
            route=DeliveryRouteInput(channel="WECOM", bot_id="bot-b295", external_user_id="wotv-b295"),
        )
        row = await session.get(TaskExecution, task.id)
        assert row is not None
        row.delivery_route_id = route_id


@dataclass(frozen=True, slots=True)
class _TaskState:
    status: str
    attempt: int
    lease_owner: str | None
    heartbeat_at: datetime | None
    error_code: str | None
    delivery_status: str
    delivery_attempts: int
    execution_snapshot_schema_version: int
    execution_snapshot_json: dict[str, Any]


async def _state(factory: async_sessionmaker[AsyncSession], task_id: uuid.UUID) -> _TaskState:
    async with factory() as session:
        row = await session.get(TaskExecution, task_id)
        assert row is not None, f"task {task_id} must exist"
        return _TaskState(
            status=str(row.status),
            attempt=int(row.attempt),
            lease_owner=row.lease_owner,
            heartbeat_at=row.heartbeat_at,
            error_code=row.error_code,
            delivery_status=str(row.delivery_status),
            delivery_attempts=int(row.delivery_attempts),
            execution_snapshot_schema_version=int(row.execution_snapshot_schema_version),
            execution_snapshot_json=dict(row.execution_snapshot_json),
        )


async def _wait_for(
    predicate: Callable[[], bool], *, timeout: float = WAIT_TIMEOUT_SEC, message: str = ""
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"condition not met within {timeout}s: {message}")


async def _wait_state(
    factory: async_sessionmaker[AsyncSession],
    task_id: uuid.UUID,
    predicate: Callable[[_TaskState], bool],
    *,
    timeout: float = WAIT_TIMEOUT_SEC,
) -> _TaskState:
    deadline = time.monotonic() + timeout
    state = await _state(factory, task_id)
    while time.monotonic() < deadline:
        if predicate(state):
            return state
        await asyncio.sleep(0.05)
        state = await _state(factory, task_id)
    raise AssertionError(f"task {task_id} never reached the expected state: {state}")


def _limits_tuple(limits: httpx.Limits) -> tuple[int, int]:
    return limits.max_connections, limits.max_keepalive_connections


def _effective_pool_limits(client: httpx.AsyncClient) -> tuple[int, int]:
    """httpx 不暴露池预算：从真实 transport 的 httpcore 池读**有效值**（装配断言的直接证据）。"""
    pool = client._transport._pool  # type: ignore[attr-defined]
    return pool._max_connections, pool._max_keepalive_connections  # type: ignore[attr-defined]


def _loop_task(name: str) -> asyncio.Task[Any]:
    tasks = [task for task in asyncio.all_tasks() if task.get_name() == name]
    assert len(tasks) == 1, f"expected exactly one {name} task, found {len(tasks)}"
    return tasks[0]


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _terminate(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, ValueError):
        os.kill(pid, signal.SIGKILL)


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


# --------------------------------------------------------------------------------------
# 1. 装配根注入分池预算、并发与期限；只建本进程调用的池
# --------------------------------------------------------------------------------------


async def test_b295_assembly_root_injects_per_pool_budgets_concurrency_and_loops(
    isolated_worker: IsolatedWorker, settings_probe: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-295] 真实 lifespan：分池预算/并发来自启动设置，五条循环各自成任务，退出即关池。"""
    monkeypatch.setenv("CONSOLE_PLATFORM_URL", settings_probe)
    monkeypatch.setenv("IM_GATEWAY_URL", DEAD_GATEWAY_URL)
    monkeypatch.setenv("WORKER_EXECUTION_CONCURRENCY", "3")
    monkeypatch.setenv("CONTROL_HTTP_MAX_CONNECTIONS", "6")
    monkeypatch.setenv("CONTROL_HTTP_MAX_KEEPALIVE", "3")
    monkeypatch.setenv("EGRESS_HTTP_MAX_CONNECTIONS", "9")
    monkeypatch.setenv("EGRESS_HTTP_MAX_KEEPALIVE", "4")
    monkeypatch.setenv("HTTP_CONNECT_TIMEOUT_SEC", "4")
    monkeypatch.setenv("HTTP_POOL_TIMEOUT_SEC", "3")
    monkeypatch.setenv("CONTROL_HTTP_READ_TIMEOUT_SEC", "7")
    monkeypatch.setenv("EGRESS_HTTP_READ_TIMEOUT_SEC", "11")
    from muad_agent_worker import main

    before = asyncio.all_tasks()
    async with main.lifespan(main.app):
        pools = main.app.state.http_pools
        settings = SharedSettings()
        assert isinstance(pools, WorkerHttpPools)
        assert pools.control is not pools.egress, "控制与业务各自建池，不共享连接"

        # 有效池大小 = 启动设置值（装配根不复制默认值）
        assert _effective_pool_limits(pools.control) == (6, 3)
        assert _effective_pool_limits(pools.egress) == (9, 4)
        assert _effective_pool_limits(pools.control) == _limits_tuple(pool_limits(settings, "control"))
        assert _effective_pool_limits(pools.egress) == _limits_tuple(pool_limits(settings, "egress"))

        # 期限：连接/池超时全进程统一，读超时按调用类别
        for client, read in ((pools.control, 7.0), (pools.egress, 11.0)):
            assert (client.timeout.read, client.timeout.connect, client.timeout.pool) == (read, 4.0, 3.0)

        # 调用方消费装配根注入的**同一实例**（不是各自再造一个池）
        assert main.app.state.platform_settings_client._client is pools.control
        assert main.app.state.scheduler_loop._resolver._client is pools.control
        assert main.app.state.delivery_loop._client._client is pools.egress
        assert main.app.state.delivery_loop._webchat_inbox._client is pools.egress
        assert main.app.state.delivery_loop._resolver._client is pools.egress
        assert main.app.state.result_dispatcher._client is pools.egress

        # 并发：任务槽上限取 WORKER_EXECUTION_CONCURRENCY
        assert main.app.state.worker_loop.slot_limit == 3

        # 期限：结果发件策略取启动设置，且租约必须覆盖单次请求
        policy = main.app.state.result_dispatcher._policy
        assert (policy.timeout_sec, policy.lease_sec) == (
            settings.async_tool_dispatch_timeout_sec,
            settings.async_tool_dispatch_lease_sec,
        )
        assert policy.timeout_sec < policy.lease_sec

        # 独立循环：领取/维护/调度/投递/结果各自成任务（互不排队尾）
        names = {task.get_name() for task in asyncio.all_tasks() - before}
        assert set(LOOP_NAMES) <= names, f"缺少独立循环: {set(LOOP_NAMES) - names}"
        assert not pools.control.is_closed and not pools.egress.is_closed
    assert pools.control.is_closed and pools.egress.is_closed, "装配根建的池随进程关闭"
    leftover = [task for task in asyncio.all_tasks() - before if not task.done()]
    assert leftover == [], f"存在悬挂后台任务: {leftover}"


def test_b295_pool_budgets_come_from_settings_and_unused_pools_fail_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[B-295] 池预算派生自启动设置；本进程不调用的池（MCP/上传/流/模型）显式拒绝。"""
    monkeypatch.setenv("CONTROL_HTTP_MAX_CONNECTIONS", "6")
    monkeypatch.setenv("CONTROL_HTTP_MAX_KEEPALIVE", "3")
    monkeypatch.setenv("CONTROL_HTTP_READ_TIMEOUT_SEC", "7")
    monkeypatch.setenv("EGRESS_HTTP_MAX_CONNECTIONS", "9")
    monkeypatch.setenv("EGRESS_HTTP_MAX_KEEPALIVE", "4")
    monkeypatch.setenv("EGRESS_HTTP_READ_TIMEOUT_SEC", "11")
    settings = SharedSettings()

    assert _limits_tuple(pool_limits(settings, "control")) == (6, 3)
    assert _limits_tuple(pool_limits(settings, "egress")) == (9, 4)
    assert pool_timeout(settings, "control").read == 7.0
    assert pool_timeout(settings, "egress").read == 11.0

    assert WORKER_HTTP_POOLS == ("control", "egress")
    assert {field.name for field in fields(WorkerHttpPools)} == {"control", "egress"}, (
        "Worker 只建实际调用的池（design §3.7「实例只创建实际需要的池」）"
    )
    for unused in ("mcp", "upload", "stream", "model"):
        with pytest.raises(ValueError, match=unused):
            pool_limits(settings, unused)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match=unused):
            pool_timeout(settings, unused)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# 2. 续租/回收/领取/投递/调度互不阻塞（投递阻塞在真实 HTTP 读上）
# --------------------------------------------------------------------------------------


async def test_b295_reclaim_claim_heartbeat_and_schedule_do_not_block_on_delivery(
    isolated_worker: IsolatedWorker,
    real_skill_cache: SkillArtifactCache,
    settings_probe: str,
    hanging_gateway: _HangingGateway,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """[B-295] 投递循环阻塞在真实 HTTP 读上时，其余四条循环照常按各自节拍推进。"""
    monkeypatch.setenv("CONSOLE_PLATFORM_URL", settings_probe)
    monkeypatch.setenv("IM_GATEWAY_URL", hanging_gateway.url)
    monkeypatch.setenv("WORKER_EXECUTION_CONCURRENCY", "1")
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SEC", "1")
    monkeypatch.setenv("RUN_REAPER_INTERVAL_SEC", "1")
    monkeypatch.setenv("TASK_HEARTBEAT_SEC", "1")
    monkeypatch.setenv("TASK_CANCEL_CHECK_SEC", "1")
    monkeypatch.setenv("SCHEDULER_POLL_INTERVAL_SEC", "1")
    monkeypatch.setenv("TASK_DEADLINE_SWEEP_INTERVAL_SEC", "1")
    monkeypatch.setenv("DELIVERY_POLL_INTERVAL_SEC", "1")
    monkeypatch.setenv("EGRESS_HTTP_READ_TIMEOUT_SEC", "30")

    factory = isolated_worker.factory
    ready = tmp_path / "holding.ready"
    holding_key, holding_sum = _publish_skill(
        tmp_path,
        body=(
            "import json, time\n"
            f"open({str(ready)!r}, 'w').write('ready')\n"
            "print(json.dumps({'ok': True}))\n"
            "time.sleep(30)\n"
        ),
    )
    failing_key, failing_sum = _publish_skill(tmp_path, body="import sys\nsys.exit(0)\n")

    # 可领取：真实 Skill 子进程（占住唯一槽，供续租断言）
    holding = await _seed_skill_task(
        factory, storage_key=holding_key, checksum=holding_sum, priority=1
    )
    # 过期租约：维护循环的回收对象（attempt=1 ⇒ 按退避重排，不终态）
    expired = await _seed_skill_task(
        factory,
        storage_key=failing_key,
        checksum=failing_sum,
        priority=200,
        status="RUNNING",
        attempt=1,
        lease_owner="b295-ghost",
        lease_until=datetime.now(UTC) - timedelta(minutes=1),
        heartbeat_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    # 过期 deadline：调度循环的 sweep 对象（claim 不再执行过期行）
    overdue = await _seed_skill_task(
        factory,
        storage_key=failing_key,
        checksum=failing_sum,
        priority=300,
        deadline_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    # 待投递：终态行 + 真实 WECOM 路由 ⇒ 投递循环走 Gateway 出站并阻塞
    deliverable = await _seed_skill_task(
        factory,
        storage_key=failing_key,
        checksum=failing_sum,
        priority=400,
        status="COMPLETED",
        finished_at=datetime.now(UTC),
    )
    await _attach_wecom_route(factory, deliverable)

    from muad_agent_worker import main

    before = asyncio.all_tasks()
    async with main.lifespan(main.app):
        # 投递循环先在真实 HTTP 上阻塞：连接已被接受、响应永不回来
        await _wait_for(lambda: hanging_gateway.accepted >= 1, message="投递循环未发出真实请求")

        # 领取循环：占住唯一槽的真实 Skill 子进程起来了
        await _wait_for(ready.exists, message="领取循环未执行 Skill")
        claimed = await _wait_state(
            factory,
            holding.id,
            lambda state: state.status == "RUNNING" and state.lease_owner is not None,
        )
        assert claimed.attempt == 1
        first_heartbeat = claimed.heartbeat_at
        assert first_heartbeat is not None

        # 续租：心跳在投递阻塞期间照常推进租约
        await _wait_state(
            factory,
            holding.id,
            lambda state: state.heartbeat_at is not None and state.heartbeat_at > first_heartbeat,
        )

        # 回收：维护循环按自己的节拍回收过期租约（不排在投递后面）
        reclaimed = await _wait_state(factory, expired.id, lambda state: state.status == "QUEUED")
        assert reclaimed.attempt == 1, "回收不重置 attempt"

        # 调度：deadline sweep 独立收尾过期行
        await _wait_state(
            factory,
            overdue.id,
            lambda state: state.status == "FAILED" and state.error_code == "TASK_DEADLINE_EXCEEDED",
        )

        # 投递循环仍在同一条真实请求上（尚未拿到任何结论）
        assert hanging_gateway.accepted >= 1
        assert not _loop_task("worker.delivery").done(), "投递循环不应因其它循环而退出"
        pending = await _state(factory, deliverable.id)
        assert pending.delivery_status == "PENDING", "投递没有结论：不得置 SENT/FAILED"
        assert pending.delivery_attempts == 1, "预留的这次尝试仍在飞"
    leftover = [task for task in asyncio.all_tasks() - before if not task.done()]
    assert leftover == [], f"存在悬挂后台任务: {leftover}"


# --------------------------------------------------------------------------------------
# 3. 进程退出清子进程组
# --------------------------------------------------------------------------------------


async def test_b295_lifespan_exit_stops_the_skill_process_group(
    isolated_worker: IsolatedWorker,
    real_skill_cache: SkillArtifactCache,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """[B-295] lifespan 退出（进程退出路径）：整组 Skill 子进程被回收，且不写伪造结论。"""
    monkeypatch.setenv("CONSOLE_PLATFORM_URL", DEAD_GATEWAY_URL)
    monkeypatch.setenv("IM_GATEWAY_URL", DEAD_GATEWAY_URL)
    monkeypatch.setenv("WORKER_EXECUTION_CONCURRENCY", "1")
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SEC", "1")
    monkeypatch.setenv("TASK_HEARTBEAT_SEC", "1")
    monkeypatch.setenv("TASK_CANCEL_CHECK_SEC", "1")

    factory = isolated_worker.factory
    parent_pid_file = tmp_path / "skill.pid"
    descendant_pid_file = tmp_path / "descendant.pid"
    ready = tmp_path / "skill.ready"
    descendant = tmp_path / "descendant.py"
    descendant.write_text(
        "import os, time\n"
        f"open({str(descendant_pid_file)!r}, 'w').write(str(os.getpid()))\n"
        f"open({str(ready)!r}, 'w').write('ready')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    storage_key, checksum = _publish_skill(
        tmp_path,
        body=(
            "import os, subprocess, sys, time\n"
            f"open({str(parent_pid_file)!r}, 'w').write(str(os.getpid()))\n"
            f"subprocess.Popen([sys.executable, {str(descendant)!r}])\n"
            "time.sleep(60)\n"
        ),
    )
    task = await _seed_skill_task(factory, storage_key=storage_key, checksum=checksum, priority=1)

    from muad_agent_worker import main

    before = asyncio.all_tasks()
    try:
        async with main.lifespan(main.app):
            pools = main.app.state.http_pools
            await _wait_for(ready.exists, message="真实 Skill 子进程与孙进程未起来")
            parent_pid = _read_pid(parent_pid_file)
            descendant_pid = _read_pid(descendant_pid_file)
            assert parent_pid is not None and descendant_pid is not None
            assert _process_alive(parent_pid) and _process_alive(descendant_pid)
            assert not pools.control.is_closed and not pools.egress.is_closed
        # 退出路径：取消领取循环 → 关闭任务槽 → 执行器收尾整组子进程
        assert parent_pid is not None and descendant_pid is not None
        await _wait_for(lambda: not _process_alive(parent_pid), message="Skill 子进程未随退出回收")
        assert not _process_alive(descendant_pid), "孙进程未随进程组回收"
        assert pools.control.is_closed and pools.egress.is_closed, "退出即关池"
        # 失约实例不写任何伪造结论：行仍是 RUNNING，等租约回收接管
        state = await _state(factory, task.id)
        assert (state.status, state.error_code) == ("RUNNING", None)
        leftover = [item for item in asyncio.all_tasks() - before if not item.done()]
        assert leftover == [], f"存在悬挂后台任务: {leftover}"
    finally:
        for pid_file in (parent_pid_file, descendant_pid_file):
            pid = _read_pid(pid_file)
            if pid is not None:
                _terminate(pid)


# --------------------------------------------------------------------------------------
# 4. Task 旧 policy 不补默认
# --------------------------------------------------------------------------------------


async def test_b295_old_task_policy_is_not_backfilled_with_new_defaults(
    isolated_worker: IsolatedWorker,
    real_skill_cache: SkillArtifactCache,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """[B-295] 冻结快照缺新键的旧行不被补默认：快照逐字节不变、未知安全性不自动重做。"""
    monkeypatch.setenv("CONSOLE_PLATFORM_URL", DEAD_GATEWAY_URL)
    monkeypatch.setenv("IM_GATEWAY_URL", DEAD_GATEWAY_URL)
    monkeypatch.setenv("WORKER_EXECUTION_CONCURRENCY", "1")
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SEC", "1")
    monkeypatch.setenv("TASK_HEARTBEAT_SEC", "1")
    monkeypatch.setenv("TASK_CANCEL_CHECK_SEC", "1")

    factory = isolated_worker.factory
    storage_key, checksum = _publish_skill(tmp_path, body="import sys\nsys.exit(1)\n")
    # 旧形状：冻结时还没有 budget.retry_safety 这一叶
    old_artifact = uuid.uuid4()
    frozen_artifact = uuid.uuid4()
    old_snapshot = _snapshot(old_artifact, storage_key, checksum, retry_safety=None)
    frozen_snapshot = _snapshot(frozen_artifact, storage_key, checksum, retry_safety="IDEMPOTENT")
    old_task = await _seed_task(
        factory, snapshot=old_snapshot, skill_artifact_id=old_artifact, priority=1
    )
    frozen_task = await _seed_task(
        factory, snapshot=frozen_snapshot, skill_artifact_id=frozen_artifact, priority=2
    )

    from muad_agent_worker import main

    async with main.lifespan(main.app):
        # 同一个确定性失败形状（脚本退出码 1），结论由**冻结** policy 决定
        old_state = await _wait_state(factory, old_task.id, lambda state: state.status == "FAILED")
        assert old_state.error_code == TASK_RESULT_UNVERIFIED, "未知安全性不得按新默认自动重做"
        await _wait_state(
            factory,
            frozen_task.id,
            lambda state: state.status == "QUEUED" and state.attempt == 1,
        )

    # 旧行不被补默认：快照与版本逐字节不变
    assert old_state.execution_snapshot_json == old_snapshot
    assert old_state.execution_snapshot_schema_version == 1
    final = await _state(factory, old_task.id)
    assert final.execution_snapshot_json == old_snapshot
    assert final.execution_snapshot_schema_version == 1
    retrying = await _state(factory, frozen_task.id)
    assert retrying.execution_snapshot_json == frozen_snapshot, "冻结声明不被改写"
    assert retrying.status == "QUEUED", "声明了 IDEMPOTENT 的冻结行按自己的值有界重试"
