"""[E-01][E-02][E-03] 依赖故障矩阵：PostgreSQL / Redis / Artifact Store（integration）。

真实边界与注入方式（**外部共享服务全程保持运行**：真实 PG 与真实 Redis 不被停止、重启或改配置）：

- **E-01 `Service → PostgreSQL`**：测试进程内起一个真实 TCP 转发器（`_TcpRelay`），被测 Worker 的
  `DATABASE_URL` 指向转发端口；`fail()` 时拒绝新连接并切断已建立连接 —— 被测服务拿到的是
  **真实的连接失败**（非替身，也不是关闭真实 PG）。`recover()` 后同一进程重新连上。
  用例内以「测试进程自己仍能 `SELECT 1`」为正对照，证明故障只发生在被测服务的边界上。
- **E-02 `Redis → PG`**：被测 Worker 的 `REDIS_URL` 指向不可达端点 `redis://127.0.0.1:1/0`
  （与 `tests/acceptance/im_gateway/test_redis_degradation.py` 同口径）。去重口径直接用生产的
  `build_dedupe_store`/`is_duplicate`（网关启动时的同一构造路径）取证降级与恢复，不手写替身。
- **E-03 `emptyDir cache → NFS`**：被测 Worker 的 Artifact 根先真实存在，随后**换成同名普通文件**
  （`is_dir()` 变假、`resolve/open` 得到 `ENOTDIR` —— 写入与 cache miss 都真实失败），复位时换回目录；
  既有 READY 的本地缓存仍可执行，cache miss 与新写入以 `SKILL_ARTIFACT_UNAVAILABLE` 明确失败。

每条注入都在 `finally` 中复位；注入前后取真实盘面（本租户 PG 行、本运行的 Redis hint/dedupe key、
Artifact 目录）并断言无残留，同时断言无孤儿 `uvicorn`/`muad_*.main` 进程。

**显式边界（design 技术债③）**：**NFS 高延迟**依赖环境能力，本机不具备可复现的真实慢故障注入，
按 manual 口径登记原因，**不以本地替身冒充 NFS 慢故障** —— 本文件不对该场景给出通过结论。
"""

from __future__ import annotations

import contextlib
import hashlib
import shutil
import socket
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import redis.asyncio as redis_client
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_common import SharedSettings
from muad_im_gateway.infrastructure.dedupe import (
    DedupeStoreError,
    RedisDedupeStore,
    build_dedupe_store,
    is_duplicate,
)
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.engine import make_url

from tests.acceptance.task_schedule.environment import ServiceProcess

from .environment import (
    DfxStack,
    TaskSpec,
    count_rows,
    count_task_events,
    post_task_request,
    read_task_row,
    resolve_task_spec,
    run_async,
    run_db,
    start_service,
    submit_task,
)

pytestmark = pytest.mark.integration

# 不可达端点（真实连接被拒，不是替身）：与 im_gateway 的 Redis 降级套件同一口径。
DEAD_REDIS_URL = "redis://127.0.0.1:1/0"
POLL_INTERVAL_SEC = 0.2
# 负载下（整跑 `tests/acceptance` 时同机并发大量服务进程）服务启动与收敛都会变慢：
# 就绪、终态与进程收敛一律等到上限再失败，而不是启动后立刻断言。
READY_TIMEOUT_SEC = 60.0
TERMINAL_TIMEOUT_SEC = 120.0
PROCESS_WAIT_SEC = 30.0
# 网关投递去重键口径（apps/im-gateway/src/muad_im_gateway/api/delivery.py）。
DELIVERY_DEDUPE_PREFIX = "delivery:dedupe"
DELIVERY_DEDUPE_TTL_SEC = 604800
# 本用例自造键的可识别前缀：收尾按前缀核对「没有残留 key」。
FAULT_KEY_PREFIX = "dfx-fault-matrix"
# 用例内换出的恢复 Worker（模块收尾前必须停掉，否则留下孤儿进程）。
_RESTORED_WORKERS: list[ServiceProcess] = []


@pytest.fixture(scope="module", autouse=True)
def _stop_restored_workers(live_stack: DfxStack) -> Iterator[None]:
    """模块收尾：停掉用例内换出的恢复 Worker（依赖 `live_stack` 以保证先于其清理执行）。"""
    yield
    for process in _RESTORED_WORKERS:
        process.stop()
    _RESTORED_WORKERS.clear()


def _new_key(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _database_url() -> str:
    return SharedSettings().require_database_url()


def _redis_url() -> str:
    return SharedSettings().require_redis_url()


def _submit(
    stack: DfxStack,
    http: httpx.Client,
    spec: TaskSpec,
    *,
    worker_url: str | None = None,
    **input_data: Any,
) -> uuid.UUID:
    """经真实 HTTP 提交一个 Task，返回 PG 里的真实 task id（提交走指定 Worker 便于归因）。"""
    submitted = submit_task(
        stack,
        http,
        spec,
        input_data=input_data,
        idempotency_key=_new_key(FAULT_KEY_PREFIX),
        worker_url=worker_url,
    )
    return uuid.UUID(str(submitted["task_id"]))


async def _select_one(factory: Any) -> bool:
    """测试进程自己连真实 PG（E-01 的正对照：真实 PG 在故障窗口内依然可用）。"""
    async with factory() as session:
        return bool(await session.scalar(text("SELECT 1")))


def _await_task_status(
    task_id: uuid.UUID, status: str, *, timeout_sec: float = TERMINAL_TIMEOUT_SEC
) -> dict[str, Any]:
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


def _probe_service(url: str, path: str = "/readyz") -> tuple[int, dict[str, Any]]:
    response = httpx.get(f"{url}{path}", timeout=5.0)
    body = cast(dict[str, Any], response.json())
    data = body.get("data")
    return response.status_code, cast(dict[str, Any], data if isinstance(data, dict) else {})


def _await_readiness(
    url: str, *, ready: bool, timeout_sec: float = READY_TIMEOUT_SEC
) -> dict[str, Any]:
    """等被测服务的 `/readyz` 到达期望状态（就绪 = 200；降级 = 非 200），有界。"""
    deadline = time.monotonic() + timeout_sec
    status, data = 0, {}
    while time.monotonic() < deadline:
        status, data = _probe_service(url)
        if (status == 200) is ready:
            return data
        time.sleep(POLL_INTERVAL_SEC)
    target = "恢复就绪" if ready else "降级"
    raise AssertionError(f"{url}/readyz 未在 {timeout_sec}s 内{target}：{status} {data}")


def _service_commands() -> list[str]:
    """当前在跑的**真实服务进程**命令行（`uvicorn` + `muad_*.main`）——孤儿进程判据。

    判据是「可执行文件是 Python 且命令行为 `-m uvicorn muad_*`」：真实服务的启动形式就是
    `python -m uvicorn muad_*.main:app`。不能只按「命令行里出现 uvicorn/muad_」过滤——调用方
    shell 自己的命令行只要提到这两个子串（例如 `grep -E "uvicorn|muad_"`），那个 shell 就会被
    算成多余的服务进程，把「无多余进程」的断言变成假失败。`ucomm` 取 argv[0] 的名字
    （`python3.13`）；`comm` 在 macOS 上被截断成 16 字符（`/Users/jahan/wor`），不能用它判定。
    """
    completed = subprocess.run(
        ["ps", "-Aww", "-o", "ucomm=,command="], capture_output=True, text=True, check=True
    )
    services: list[str] = []
    for line in completed.stdout.splitlines():
        ucomm, _, command = line.strip().partition(" ")
        if "python" not in ucomm.lower():
            continue
        if "-m uvicorn muad_" in command:
            services.append(command.strip())
    return services


def _await_service_processes(expected: dict[str, int]) -> list[str]:
    """等进程收敛到期望分布（停进程后端口/进程表有极短抖动窗口）。"""
    deadline = time.monotonic() + PROCESS_WAIT_SEC
    commands: list[str] = []
    while time.monotonic() < deadline:
        commands = _service_commands()
        if {module: sum(module in line for line in commands) for module in expected} == expected:
            return commands
        time.sleep(POLL_INTERVAL_SEC)
    raise AssertionError(f"服务进程未收敛到 {expected}：{commands}")


def _artifact_files(root: Path) -> list[str]:
    """Artifact 根的相对文件名清单（真实文件系统盘面）。"""
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


async def _cancel_hint_keys(task_ids: list[uuid.UUID]) -> list[str]:
    """本运行提交过的 Task 在真实 Redis 里残留的 `task:cancel:{id}`（精确到本运行的 id）。"""
    client = redis_client.from_url(_redis_url(), decode_responses=True)
    try:
        keys = [f"task:cancel:{task_id}" for task_id in task_ids]
        return [key for key in keys if await client.exists(key)]
    finally:
        await client.aclose()


async def _dedupe_keys() -> list[str]:
    """本用例自造的投递去重键（前缀可识别，不与其它套件的键混淆）。"""
    client = redis_client.from_url(_redis_url(), decode_responses=True)
    try:
        return sorted(await client.keys(f"{DELIVERY_DEDUPE_PREFIX}:{FAULT_KEY_PREFIX}*"))
    finally:
        await client.aclose()


async def _delivery_keys_for(task_ids: list[uuid.UUID]) -> list[str]:
    """本运行提交的 Task 对应的投递去重键（`delivery_mode=NONE` 不该产生任何一条）。"""
    client = redis_client.from_url(_redis_url(), decode_responses=True)
    try:
        keys = [f"{DELIVERY_DEDUPE_PREFIX}:task:{task_id}:final" for task_id in task_ids]
        return [key for key in keys if await client.exists(key)]
    finally:
        await client.aclose()


def _assert_residue_free(
    stack: DfxStack, task_ids: list[uuid.UUID], *, artifacts_before: list[str]
) -> None:
    """复位后的真实盘面核对：无残留 key、Artifact 根未变、无多余服务进程。"""
    cancel_hints = cast(list[str], run_async(lambda: _cancel_hint_keys(task_ids)))
    assert cancel_hints == [], f"本运行的 cancel hint 未清理：{cancel_hints}"
    dedupe_keys = cast(list[str], run_async(_dedupe_keys))
    assert dedupe_keys == [], f"本用例的投递去重键未清理：{dedupe_keys}"
    delivery_keys = cast(list[str], run_async(lambda: _delivery_keys_for(task_ids)))
    assert delivery_keys == [], f"本运行的 Task 不该产生投递键（delivery_mode=NONE）：{delivery_keys}"
    assert _artifact_files(stack.artifact_root) == artifacts_before, "真实 Artifact 根被本用例改动"
    commands = _await_service_processes(
        {"muad_console_platform.main": 1, "muad_agent_worker.main": 1}
    )
    assert len(commands) == 2, f"注入复位后仍有多余服务进程：{commands}"


@contextmanager
def _degraded_worker(
    stack: DfxStack, tmp_path: Path, *, name: str, **overrides: str
) -> Iterator[ServiceProcess]:
    """停下基座 Worker，用**同一端口 + 同一基座环境**起一个注入了故障端点的 Worker。

    退出时先停注入进程，再用基座环境把 Worker 起回来（恢复点是真实的：HTTP 与进程都在）。
    基座 Worker 的 `env`/`port` 原样复用，不另造第二套进程管理。
    """
    live = stack.processes["worker"]
    live.stop()
    degraded = ServiceProcess(
        name=name,
        module="muad_agent_worker.main",
        port=live.port,
        env={**live.env, **overrides},
        log_path=tmp_path / f"{name}.log",
    )
    try:
        start_service(degraded)
        yield degraded
    finally:
        degraded.stop()
        restored = ServiceProcess(
            name="worker",
            module="muad_agent_worker.main",
            port=live.port,
            env=live.env,
            log_path=tmp_path / "worker-restored.log",
        )
        start_service(restored)
        _RESTORED_WORKERS.append(restored)
        stack.processes["worker"] = restored


def _pipe(source: socket.socket, target: socket.socket) -> None:
    try:
        while True:
            chunk = source.recv(65536)
            if not chunk:
                return
            target.sendall(chunk)
    except OSError:
        return


def _shutdown(sock: socket.socket) -> None:
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)
    with contextlib.suppress(OSError):
        sock.close()


class _TcpRelay:
    """测试进程内的真实 TCP 转发：`fail()` 制造真实的连接失败，`recover()` 复位。

    上游（真实 PostgreSQL）全程不被触碰 —— 被注入的只是「被测进程 → 上游」这段连接：
    `fail()` 后新连接被立刻断开、已建立的连接被切断，被测服务拿到的是真实的网络错误。
    """

    def __init__(self, host: str, port: int) -> None:
        self._upstream = (host, port)
        self._listener: socket.socket | None = None
        self._live: set[socket.socket] = set()
        self._accepting = False
        self._lock = threading.Lock()
        self.port = 0

    def start(self) -> None:
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(64)
        self.port = int(listener.getsockname()[1])
        with self._lock:
            self._listener = listener
            self._accepting = True
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def fail(self) -> None:
        with self._lock:
            self._accepting = False
            live = list(self._live)
            self._live.clear()
        for sock in live:
            _shutdown(sock)

    def recover(self) -> None:
        with self._lock:
            self._accepting = True

    def close(self) -> None:
        self.fail()
        with self._lock:
            listener, self._listener = self._listener, None
        if listener is not None:
            _shutdown(listener)

    def _accept_loop(self) -> None:
        while True:
            with self._lock:
                listener = self._listener
            if listener is None:
                return
            try:
                client, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=self._pump, args=(client,), daemon=True).start()

    def _pump(self, client: socket.socket) -> None:
        upstream: socket.socket | None = None
        try:
            upstream = self._attach(client)
            if upstream is None:
                return
            threads = (
                threading.Thread(target=_pipe, args=(client, upstream), daemon=True),
                threading.Thread(target=_pipe, args=(upstream, client), daemon=True),
            )
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        finally:
            if upstream is not None:
                self._forget(upstream)
            self._forget(client)
            _shutdown(client)
            if upstream is not None:
                _shutdown(upstream)

    def _attach(self, client: socket.socket) -> socket.socket | None:
        """建立上游连接并登记；故障中（或已停止）时不登记，直接断开客户端。"""
        with self._lock:
            if not self._accepting:
                _shutdown(client)
                return None
        try:
            upstream = socket.create_connection(self._upstream, timeout=5.0)
        except OSError:
            _shutdown(client)
            return None
        upstream.settimeout(None)
        with self._lock:
            if not self._accepting:
                _shutdown(upstream)
                _shutdown(client)
                return None
            self._live.update({client, upstream})
        return upstream

    def _forget(self, sock: socket.socket) -> None:
        with self._lock:
            self._live.discard(sock)


def _assert_outage_is_fail_closed(
    stack: DfxStack,
    http: httpx.Client,
    spec: TaskSpec,
    worker_url: str,
    *,
    cache_root: Path,
    probe_key: str,
) -> None:
    """故障窗口：就绪失败、存活不受影响、真实 PG 仍可用、写入显式失败且不落任何状态。"""
    detail = _await_readiness(worker_url, ready=False)
    assert "database" in detail["failed"], f"就绪失败项未含 database：{detail}"
    status, _ = _probe_service(worker_url, "/healthz")
    assert status == 200, "存活与依赖必须分离：依赖故障不应让 /healthz 失败"
    assert run_db(_select_one) is True, "正对照：真实 PostgreSQL 未被本次注入影响"
    response = post_task_request(
        stack,
        http,
        spec,
        input_data={"probe": "during-outage"},
        idempotency_key=probe_key,
        worker_url=worker_url,
    )
    assert response.status_code == 500, f"PG 不可用时提交必须显式失败：{response.status_code}"
    assert response.json()["code"] == "COMMON_INTERNAL_ERROR", response.text
    for table in ("task.task_execution", "task.task_submission"):
        written_rows = count_rows(table, stack.tenant_id, "idempotency_key = :k", {"k": probe_key})
        assert written_rows == 0, f"PG 不可用时仍写入了 {table} —— 没有 fail closed"
    leftovers = [path.name for path in cache_root.rglob("*")] if cache_root.exists() else []
    assert leftovers == [], f"故障窗口内落下了本地状态：{leftovers}"


def _assert_recovered_worker_executes(
    stack: DfxStack, http: httpx.Client, spec: TaskSpec, worker_url: str, *, cache_root: Path
) -> uuid.UUID:
    """恢复后：真实执行重新可用，且本地缓存重新产生（证明故障窗口内确实什么都没落）。"""
    task_id = _submit(stack, http, spec, worker_url=worker_url, probe="after-recovery")
    row = _await_task_status(task_id, "COMPLETED")
    assert row["result_json"] == {"slept_sec": 0.0, "probe": "after-recovery"}
    assert cache_root.exists(), "恢复后真实执行应重新产生本地缓存（此前为空）"
    return task_id


def test_e01_postgres_unavailable_fails_closed_and_recovers(
    live_stack: DfxStack, http: httpx.Client, tmp_path: Path
) -> None:
    """E-01：被测 Worker 与真实 PG 之间的连接失败 → fail closed、`/readyz` 失败、恢复后事实完整。"""
    spec = resolve_task_spec(live_stack, http)
    artifacts_before = _artifact_files(live_stack.artifact_root)
    # 故障前先在真实 PG 留下一笔已完成事实（恢复后要逐字段仍然完整）。
    pre_fault_id = _submit(live_stack, http, spec, probe="pre-fault")
    pre_fault_row = _await_task_status(pre_fault_id, "COMPLETED")

    url = make_url(_database_url())
    relay = _TcpRelay(host=cast(str, url.host), port=cast(int, url.port or 5432))
    relay.start()
    poisoned = url.set(host="127.0.0.1", port=relay.port).render_as_string(hide_password=False)
    cache_root = tmp_path / "pg-outage-cache"
    task_ids: list[uuid.UUID] = [pre_fault_id]
    try:
        with _degraded_worker(
            live_stack,
            tmp_path,
            name="worker-pg-outage",
            DATABASE_URL=poisoned,
            ARTIFACT_ROOT=str(live_stack.artifact_root),
            SKILL_CACHE_ROOT=str(cache_root),
        ) as degraded:
            assert _await_readiness(degraded.url, ready=True)["status"] == "ready", "转发可达时应就绪"
            relay.fail()
            _assert_outage_is_fail_closed(
                live_stack,
                http,
                spec,
                degraded.url,
                cache_root=cache_root,
                probe_key=_new_key(FAULT_KEY_PREFIX),
            )
            relay.recover()
            assert _await_readiness(degraded.url, ready=True)["status"] == "ready"
            assert read_task_row(pre_fault_id) == pre_fault_row, "恢复后故障前的业务事实被改动"
            task_ids.append(
                _assert_recovered_worker_executes(
                    live_stack, http, spec, degraded.url, cache_root=cache_root
                )
            )
    finally:
        relay.close()
    _assert_residue_free(live_stack, task_ids, artifacts_before=artifacts_before)


async def _unreachable_redis_probe() -> dict[str, str]:
    """不可达端点上是**真实**的连接失败：启动期降级为 Null、运行期去重显式抛错（不静默吞）。"""
    client = redis_client.from_url(DEAD_REDIS_URL, decode_responses=True)
    outcome: dict[str, str] = {}
    try:
        try:
            await client.ping()
            outcome["ping"] = "connected"
        except (RedisError, OSError) as exc:
            outcome["ping"] = type(exc).__name__
        # 运行期路径：客户端已建好但 Redis 掉线（Gateway `delivery.py` 捕获的正是这个异常）。
        try:
            await RedisDedupeStore(client).reserve(
                f"{DELIVERY_DEDUPE_PREFIX}:{_new_key(FAULT_KEY_PREFIX)}", 30
            )
            outcome["runtime_reserve"] = "reserved"
        except DedupeStoreError as exc:
            outcome["runtime_reserve"] = type(exc).__name__
    finally:
        await client.aclose()
    # 启动期路径：`build_dedupe_store`（网关启动的同一构造）在 ping 失败时给出 Null 降级。
    startup = await build_dedupe_store(DEAD_REDIS_URL)
    try:
        key = f"{DELIVERY_DEDUPE_PREFIX}:{_new_key(FAULT_KEY_PREFIX)}"
        outcome["startup_store"] = type(startup).__name__
        outcome["startup_duplicate"] = str(await is_duplicate(startup, key, DELIVERY_DEDUPE_TTL_SEC))
    finally:
        await startup.aclose()
    return outcome


async def _dedupe_degrade_and_restore() -> dict[str, Any]:
    """用生产构造路径取证：Redis 不可用 → Null 降级（at-least-once）；恢复 → 去重重新生效。"""
    key = f"{DELIVERY_DEDUPE_PREFIX}:{_new_key(FAULT_KEY_PREFIX)}"
    degraded = await build_dedupe_store(DEAD_REDIS_URL)
    restored = await build_dedupe_store(_redis_url())
    try:
        return {
            "degraded_store": type(degraded).__name__,
            "degraded_duplicate": await is_duplicate(degraded, key, DELIVERY_DEDUPE_TTL_SEC),
            "restored_store": type(restored).__name__,
            "restored_first_duplicate": await is_duplicate(restored, key, DELIVERY_DEDUPE_TTL_SEC),
            "restored_second_duplicate": await is_duplicate(restored, key, DELIVERY_DEDUPE_TTL_SEC),
        }
    finally:
        await restored.release(key)
        await degraded.aclose()
        await restored.aclose()


def test_e02_redis_unavailable_degrades_to_at_least_once_and_recovers(
    live_stack: DfxStack, http: httpx.Client, tmp_path: Path
) -> None:
    """E-02：Redis 不可达 → 去重降级 at-least-once、PG 事实不丢、恢复后去重重新生效。"""
    spec = resolve_task_spec(live_stack, http)
    artifacts_before = _artifact_files(live_stack.artifact_root)
    unreachable = cast(dict[str, str], run_async(_unreachable_redis_probe))
    assert unreachable["ping"] == "ConnectionError", f"注入端点必须真实不可达：{unreachable}"
    assert unreachable["runtime_reserve"] == "DedupeStoreError", (
        f"运行期去重必须在 Redis 不可用时显式失败：{unreachable}"
    )
    assert unreachable["startup_store"] == "NullDedupeStore", unreachable
    assert unreachable["startup_duplicate"] == "False", (
        f"降级时不得假称重复而丢弃投递：{unreachable}"
    )
    task_ids: list[uuid.UUID] = []
    with _degraded_worker(
        live_stack,
        tmp_path,
        name="worker-no-redis",
        REDIS_URL=DEAD_REDIS_URL,
        SKILL_CACHE_ROOT=str(tmp_path / "no-redis-cache"),
    ) as degraded:
        ready = _await_readiness(degraded.url, ready=True)
        assert ready["wakeup_hint"] == "disabled", f"降级模式必须被明确上报：{ready}"
        status, _ = _probe_service(degraded.url, "/healthz")
        assert status == 200, "Redis 只是 hint，不该让 /healthz 失败"
        task_id = _submit(live_stack, http, spec, worker_url=degraded.url, probe="no-redis")
        task_ids.append(task_id)
        row = _await_task_status(task_id, "COMPLETED")
        assert row["result_json"] == {"slept_sec": 0.0, "probe": "no-redis"}, (
            "Redis 不可用时 PG 事实必须完整（权威源在 PG）"
        )
        assert row["finished_at"] is not None
        dedupe = cast(dict[str, Any], run_async(_dedupe_degrade_and_restore))
        assert dedupe["degraded_store"] == "NullDedupeStore", dedupe
        assert dedupe["degraded_duplicate"] is False, f"不得假称重复而丢弃投递：{dedupe}"
        assert dedupe["restored_store"] == "RedisDedupeStore", dedupe
        assert dedupe["restored_first_duplicate"] is False, dedupe
        assert dedupe["restored_second_duplicate"] is True, f"恢复后去重未重新生效：{dedupe}"
    _assert_residue_free(live_stack, task_ids, artifacts_before=artifacts_before)


def _stage_artifact(stack: DfxStack, store_root: Path) -> None:
    """把本租户真实 Skill 包拷贝到注入用的 Artifact 根（同 storage_key、逐字节同 checksum）。"""
    source = stack.artifact_root / stack.storage_key
    target = store_root / stack.storage_key
    assert source.is_file(), f"真实产物根缺少 {stack.storage_key}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    assert _digest(target) == stack.checksum, "拷贝后的包 checksum 与 PG 登记的 checksum 不一致"


def _materialize_cache(stack: DfxStack, store_root: Path, cache_root: Path) -> Path:
    """用生产 `SkillArtifactCache` 把包落到本地缓存（真实读取 + checksum 校验 + 落 READY）。"""
    cache = SkillArtifactCache(NfsArtifactStore(store_root), cache_root)
    return cast(
        Path,
        run_async(
            lambda: cache.ensure(
                artifact_id=str(stack.skill_artifact_id),
                storage_key=stack.storage_key,
                checksum=stack.checksum,
            )
        ),
    )


@dataclass(frozen=True)
class _StoreOutage:
    """E-03 的注入面：真实包根（可被换成同名普通文件）、换位路径与两个本地 emptyDir 缓存。"""

    store_root: Path
    detached: Path
    warm_cache: Path
    cold_cache: Path
    ready_dir: Path
    ready_marker: str


def _prepare_store_outage(stack: DfxStack, tmp_path: Path) -> _StoreOutage:
    """搭好注入面：真实包的逐字节拷贝 + 生产 `SkillArtifactCache` 真实落 READY 的温缓存。"""
    root = tmp_path / "store-outage"
    outage = _StoreOutage(
        store_root=root / "artifacts",
        detached=root / "artifacts-detached",
        warm_cache=root / "cache-warm",
        cold_cache=root / "cache-cold",
        ready_dir=root / "cache-warm",
        ready_marker="",
    )
    _stage_artifact(stack, outage.store_root)
    ready_dir = _materialize_cache(stack, outage.store_root, outage.warm_cache)
    return replace(
        outage,
        ready_dir=ready_dir,
        ready_marker=(ready_dir / "READY").read_text(encoding="utf-8"),
    )


def _detach_store(store_root: Path, detached: Path) -> None:
    """Artifact 根由目录变成同名普通文件：读取得到 `ENOTDIR`、`is_dir()` 为假（真实不可用）。"""
    store_root.rename(detached)
    store_root.write_text("artifact store detached for fault injection\n", encoding="utf-8")


def _reattach_store(store_root: Path, detached: Path) -> None:
    """复位 Artifact 根（幂等）：注入用的占位文件在则删掉，再把上游目录换回来。"""
    if store_root.is_file():
        store_root.unlink()
    if detached.exists():
        detached.rename(store_root)


def _assert_store_outage_visible(stack: DfxStack, worker_url: str, *, outage: _StoreOutage) -> None:
    """注入后的边界取证：该 Worker 的就绪失败项指向被换掉的 Artifact 根，且源确实不可读。"""
    detail = _await_readiness(worker_url, ready=False)
    assert "artifact_storage" in detail["failed"], f"就绪失败项未含 artifact_storage：{detail}"
    assert detail["artifact_root"] == str(outage.store_root), detail
    assert not outage.store_root.is_dir(), "注入后 Artifact 根必须真实不可用（目录 → 普通文件）"
    assert (outage.detached / stack.storage_key).is_file(), "注入只是换了路径，包本身仍在"
    assert not (outage.store_root / stack.storage_key).exists(), "上游源文件必须真实不可读"


def _arm_ready_cache_keeps_running(
    stack: DfxStack, http: httpx.Client, spec: TaskSpec, tmp_path: Path, outage: _StoreOutage
) -> uuid.UUID:
    """臂 A：本地 emptyDir 已有 READY 的 Pod —— Store 不可用仍能继续执行，缓存不被重建。"""
    with _degraded_worker(
        stack,
        tmp_path,
        name="worker-nfs-warm",
        ARTIFACT_ROOT=str(outage.store_root),
        SKILL_CACHE_ROOT=str(outage.warm_cache),
    ) as warm:
        assert _await_readiness(warm.url, ready=True)["status"] == "ready"
        _detach_store(outage.store_root, outage.detached)
        _assert_store_outage_visible(stack, warm.url, outage=outage)
        task_id = _submit(stack, http, spec, worker_url=warm.url, probe="cache-hit")
        row = _await_task_status(task_id, "COMPLETED")
        assert row["result_json"] == {"slept_sec": 0.0, "probe": "cache-hit"}
        marker = (outage.ready_dir / "READY").read_text(encoding="utf-8")
        assert marker == outage.ready_marker, "既有 READY 缓存被重建了"
        return task_id


def _arm_cache_miss_fails_explicitly(
    stack: DfxStack, http: httpx.Client, spec: TaskSpec, tmp_path: Path, outage: _StoreOutage
) -> uuid.UUID:
    """臂 B：本地 emptyDir 全新（同 artifact）—— cache miss 必须明确失败，不留半成品/假 READY。

    启动校验要求 Artifact 根已挂载，故先复位存储让它起得来，再在提交前断掉存储，
    使故障窗口覆盖 cache miss 的整个发生过程。
    """
    _reattach_store(outage.store_root, outage.detached)
    with _degraded_worker(
        stack,
        tmp_path,
        name="worker-nfs-cold",
        ARTIFACT_ROOT=str(outage.store_root),
        SKILL_CACHE_ROOT=str(outage.cold_cache),
        TASK_MAX_ATTEMPTS="1",
    ) as cold:
        assert _await_readiness(cold.url, ready=True)["status"] == "ready"
        _detach_store(outage.store_root, outage.detached)
        _assert_store_outage_visible(stack, cold.url, outage=outage)
        task_id = _submit(stack, http, spec, worker_url=cold.url, probe="cache-miss")
        row = _await_task_status(task_id, "FAILED")
        assert row["error_code"] == "SKILL_ARTIFACT_UNAVAILABLE", row
        assert row["result_json"] is None, "失败路径不得返回假成功的结果"
        assert row["finished_at"] is not None
        assert row["max_attempts"] == 1, row
        assert count_task_events(task_id, "FAILED") == 1
        assert list(outage.cold_cache.iterdir()) == [], "cache miss 不得留下半成品或假 READY"
        return task_id


def _arm_new_write_succeeds_after_recovery(
    stack: DfxStack, http: httpx.Client, spec: TaskSpec, tmp_path: Path, outage: _StoreOutage
) -> uuid.UUID:
    """臂 C：存储复位后，同一空缓存重新回源成功并落下 READY（新 Artifact 写入恢复）。"""
    _reattach_store(outage.store_root, outage.detached)
    with _degraded_worker(
        stack,
        tmp_path,
        name="worker-nfs-restored",
        ARTIFACT_ROOT=str(outage.store_root),
        SKILL_CACHE_ROOT=str(outage.cold_cache),
    ) as recovered:
        assert _await_readiness(recovered.url, ready=True)["status"] == "ready"
        task_id = _submit(stack, http, spec, worker_url=recovered.url, probe="cache-miss-recovered")
        row = _await_task_status(task_id, "COMPLETED")
        assert row["result_json"] == {"slept_sec": 0.0, "probe": "cache-miss-recovered"}
        digest = stack.checksum.removeprefix("sha256:")
        assert (outage.cold_cache / digest / "READY").exists(), "恢复后应落下 READY"
        return task_id


def test_e03_artifact_store_unavailable_keeps_ready_and_fails_new_writes(
    live_stack: DfxStack, http: httpx.Client, tmp_path: Path
) -> None:
    """E-03：Artifact Store 不可用 → 既有 READY 继续执行；cache miss/新写入明确失败。"""
    spec = resolve_task_spec(live_stack, http)
    artifacts_before = _artifact_files(live_stack.artifact_root)
    outage = _prepare_store_outage(live_stack, tmp_path)
    task_ids: list[uuid.UUID] = []
    try:
        for arm in (
            _arm_ready_cache_keeps_running,
            _arm_cache_miss_fails_explicitly,
            _arm_new_write_succeeds_after_recovery,
        ):
            task_ids.append(arm(live_stack, http, spec, tmp_path, outage))
    finally:
        _reattach_store(outage.store_root, outage.detached)
    _assert_residue_free(live_stack, task_ids, artifacts_before=artifacts_before)
