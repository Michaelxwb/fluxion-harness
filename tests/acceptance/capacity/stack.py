"""[B-307] 四单元独立验收栈与故障进程控制（TASK-107；设计 §2.5 验收条件 / §4.3 分层验收与报告）。

栈的组成（全部真实，不用替身）：
- **数据存储**：`tests/acceptance/datastores.create_datastore` 建空库 + `alembic upgrade head`，
  并按验收约定占 10–15 号隔离 Redis 号位。验收会话里**已有**独立库（`muad_acc_*` + 10–15 号号位）
  时复用它 —— 同一进程里的 seed 走 `SharedSettings()`、服务子进程走注入 env，用两份库会裂脑；
  独立运行时（`tests/capacity`）按同一机制自建，并在收尾 drop。
- **四单元**：Console/Runtime/Worker/Gateway 各是独立 uvicorn 子进程（`start_new_session=True`
  ⇒ 独立进程组，收尾按组停），端口由栈内探测空闲端口分配；实例数来自锁定 profile 的
  `replica_counts`（超过 `max_replica_counts` 在启动前显式拒绝，不静默截断）。
- **共享卷**：四单元同挂 `ARTIFACT_ROOT`（RWX 口径），每进程 `SKILL_CACHE_ROOT` 独立。
- **入口代理**：`EntryProxy` 是真实反向代理子进程，分流表直接读 `deploy/k8s/base/ingress.yaml`
  （最长前缀优先，与 ingress-nginx 同口径），控制面 `GET /__proxy__/state` 只回路由与计数。
- **HTTP 协议探针**：`python -m tests.acceptance.capacity.probes` 子进程（TASK-106 的真实模型探针）。
- **故障进程控制**：`kill_instance`（SIGKILL 整组）/`stop_instance`（SIGTERM 整组后兜底 SIGKILL）
  作用于**整进程组**；`replace_instance` 起新实例（新端口）继续共享同一库/卷/Redis；
  `teardown` 幂等，只清本轮进程与本轮数据，不写共享 dev 库。

子进程模式：`python -m tests.acceptance.capacity.stack proxy --port N --backend unit=URL ...`
在真实监听后打印 `{"url": ...}`，供栈内与跨进程驱动。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

import asyncpg
import httpx
import redis.asyncio
import yaml

from tests.acceptance.datastores import (
    DEFAULT_PREFIX,
    REDIS_DB_COUNT,
    REDIS_DB_START,
    create_datastore,
    drop_datastore,
)

if TYPE_CHECKING:  # 只在类型层引用，避免栈模块反向依赖容量 harness
    from tests.capacity.profile import CapacityProfile

__all__ = [
    "DEV_DEFAULT_PORTS",
    "INGRESS_MANIFEST",
    "REPO_ROOT",
    "STACK_ID_ENV",
    "UNITS",
    "CapacityStack",
    "DatastoreHandle",
    "EntryProxy",
    "IngressRoute",
    "ProbeProcess",
    "ServiceInstance",
    "StackError",
    "acceptance_databases",
    "acquire_datastore",
    "database_exists",
    "free_port",
    "load_ingress_routes",
    "main",
    "port_is_free",
    "process_command_line",
    "query_value",
    "read_process_environ",
    "redis_value",
    "run_async",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
UNITS: tuple[str, ...] = ("console-platform", "agent-runtime", "agent-worker", "im-gateway")
UNIT_MODULES: Mapping[str, str] = {
    "console-platform": "muad_console_platform.main",
    "agent-runtime": "muad_agent_runtime.main",
    "agent-worker": "muad_agent_worker.main",
    "im-gateway": "muad_im_gateway.main",
}
#: dev 栈默认端口（8000–8003）：验收栈必须避开它们，免得与本地 dev 服务互相抢端口。
DEV_DEFAULT_PORTS: Mapping[str, int] = {
    "console-platform": 8000,
    "agent-runtime": 8001,
    "agent-worker": 8002,
    "im-gateway": 8003,
}
INGRESS_MANIFEST = REPO_ROOT / "deploy" / "k8s" / "base" / "ingress.yaml"
INGRESS_SERVICE_UNITS: Mapping[str, str] = {
    "muad-console-platform": "console-platform",
    "muad-im-gateway": "im-gateway",
}
STACK_ID_ENV = "MUAD_CAPACITY_STACK"
HEALTH_PATH = "/healthz"
PROXY_STATE_PATH = "/__proxy__/state"
PROBE_STATE_PATH = "/__probe__/state"
READY_TIMEOUT_SEC = 60.0
STOP_TIMEOUT_SEC = 15.0
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)


class StackError(RuntimeError):
    """独立验收栈的状态/生命周期错误（启动失败、重复启动、读取失败等）。"""


# --------------------------------------------------------------------------------------
# 数据存储：复用验收自动空库 + alembic + 10–15 号隔离 Redis 号位
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DatastoreHandle:
    """本轮数据存储句柄；`owns=False` 表示复用验收会话已建好的库（收尾不得 drop）。"""

    name: str
    database_url: str
    redis_url: str
    admin_database_url: str
    owns: bool


def run_async(factory: Any) -> Any:
    """在**独立线程**的新事件循环里跑协程（同 `datastores._run_in_thread` 口径）。

    不能用 `asyncio.run`：它会把主线程的事件循环置空，其后异步套件集体报
    `no current event loop`（datastores 的注释记录过实测 95 failed）。
    """
    box: list[Any] = []
    failure: list[BaseException] = []

    def target() -> None:
        loop = asyncio.new_event_loop()
        try:
            box.append(loop.run_until_complete(factory()))
        except BaseException as exc:  # noqa: BLE001 - 跨线程原样回抛
            failure.append(exc)
        finally:
            loop.close()

    thread = threading.Thread(target=target, name="capacity-stack-db")
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    return box[0] if box else None


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _redis_db_index(url: str) -> int:
    raw = urlsplit(url).path.lstrip("/")
    return int(raw) if raw else 0


def _acceptance_datastore_from_env() -> DatastoreHandle | None:
    """验收会话已建立独立库（`muad_acc_*` + 10–15 号号位）时复用；否则返回 None。"""
    from muad_common import SharedSettings

    settings = SharedSettings()
    database_url, redis_url = settings.database_url, settings.redis_url
    if not database_url or not redis_url:
        return None
    name = urlsplit(database_url).path.lstrip("/")
    index = _redis_db_index(redis_url)
    if not name.startswith(DEFAULT_PREFIX):
        return None
    if not REDIS_DB_START <= index < REDIS_DB_START + REDIS_DB_COUNT:
        return None
    # 复用句柄不拥有该库：`admin_database_url` 只用于只读查询，绝不用于 drop。
    return DatastoreHandle(
        name=name,
        database_url=database_url,
        redis_url=redis_url,
        admin_database_url=database_url,
        owns=False,
    )


def acquire_datastore() -> DatastoreHandle:
    """取得本栈的独立数据存储：优先复用验收会话的，独立运行时按同一机制自建。"""
    reused = _acceptance_datastore_from_env()
    if reused is not None:
        return reused
    info = create_datastore()
    return DatastoreHandle(
        name=info["name"],
        database_url=info["database_url"],
        redis_url=info["redis_url"],
        admin_database_url=info["admin_database_url"],
        owns=True,
    )


def query_value(database_url: str, sql: str, *params: object) -> Any:
    """只读单值查询（真实 PostgreSQL）；参数化绑定，不拼 SQL。"""

    async def query() -> Any:
        connection = await asyncpg.connect(_asyncpg_dsn(database_url), timeout=10)
        try:
            return await connection.fetchval(sql, *params)
        finally:
            await connection.close()

    return run_async(query)


def database_exists(admin_database_url: str, name: str) -> bool:
    return bool(
        query_value(
            admin_database_url, "SELECT count(*) FROM pg_database WHERE datname = $1", name
        )
    )


def acceptance_databases(admin_database_url: str) -> tuple[str, ...]:
    """当前服务器上的验收临时库名（按前缀），用于「收尾不留本轮残留」的核对。"""

    async def fetch() -> list[str]:
        connection = await asyncpg.connect(_asyncpg_dsn(admin_database_url), timeout=10)
        try:
            rows = await connection.fetch(
                "SELECT datname FROM pg_database WHERE datname LIKE $1 ORDER BY datname",
                f"{DEFAULT_PREFIX}%",
            )
            return [str(row["datname"]) for row in rows]
        finally:
            await connection.close()

    return tuple(run_async(fetch))


def redis_value(redis_url: str, key: str) -> str | None:
    """读一个 Redis 键（用于占号键核对）；不存在回 None。"""

    async def fetch() -> str | None:
        client = redis.asyncio.from_url(redis_url)
        try:
            value = await client.get(key)
        finally:
            await client.aclose()
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    return run_async(fetch)


# --------------------------------------------------------------------------------------
# 真实进程观测：端口、命令行、OS 可见的 env
# --------------------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def port_is_free(port: int) -> bool:
    """端口能否重新 bind：判「进程与监听都真的没了」，不看栈内记账。"""
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def process_command_line(pid: int) -> str:
    # `-ww` 关闭 ps 的终端宽度截断：CI runner 的非 tty 宽度会把
    # `python -m uvicorn <module>:app` 截成 `... -m uvicorn mu`，让入口断言假红。
    completed = subprocess.run(
        ["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True
    )
    if completed.returncode != 0:
        raise StackError(f"无法读取进程 {pid} 的命令行：{completed.stderr.strip()}")
    return completed.stdout.strip()


def _parse_environ_tokens(text: str) -> dict[str, str]:
    return {key: value for key, value in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)", text)}


def read_process_environ(pid: int) -> dict[str, str]:
    """读真实运行进程的 env：Linux 走 `/proc/<pid>/environ`，macOS 走 `ps eww`。"""
    if Path("/proc").is_dir():
        raw = Path(f"/proc/{pid}/environ").read_bytes()
        pairs = (chunk.split(b"=", 1) for chunk in raw.split(b"\0") if b"=" in chunk)
        return {key.decode(): value.decode() for key, value in pairs}
    completed = subprocess.run(["ps", "eww", "-p", str(pid)], capture_output=True, text=True)
    if completed.returncode != 0:
        raise StackError(f"无法读取进程 {pid} 的环境：{completed.stderr.strip()}")
    return _parse_environ_tokens(completed.stdout)


def _probe_http(url: str, *, timeout_sec: float = 2.0) -> httpx.Response | None:
    """真实 HTTP 探测；连接类错误回 None（由调用方按超时处理）。"""
    try:
        return httpx.get(url, timeout=timeout_sec, trust_env=False)
    except httpx.HTTPError:
        return None


def _wait_for_http(
    url: str,
    *,
    timeout_sec: float,
    process: subprocess.Popen[bytes],
    label: str,
    log_tail: Callable[[], str],
) -> None:
    """轮询真实 HTTP 就绪；进程提前退出或超时都显式失败并带回日志尾部。"""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise StackError(f"{label} 提前退出（code={process.returncode}）：{log_tail()}")
        response = _probe_http(url)
        if response is not None and response.status_code == 200:
            return
        time.sleep(0.1)
    raise StackError(f"{label} 在 {timeout_sec:.0f}s 内未就绪（{url}）：{log_tail()}")


def _signal_group(pgid: int, sig: int) -> None:
    """向整进程组发信号；组已消失时是空操作（停机路径幂等）。"""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return


def _spawn_child(
    argv: Sequence[str], *, env: Mapping[str, str], log_path: Path
) -> tuple[subprocess.Popen[bytes], int]:
    """起真实子进程：独立会话/进程组 + 输出落盘，返回 (process, pgid)。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            list(argv),
            cwd=REPO_ROOT,
            env=dict(env),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return process, os.getpgid(process.pid)


def _stop_child(process: subprocess.Popen[bytes], pgid: int, *, timeout_sec: float) -> None:
    """SIGTERM 整组 → 有界等待 → SIGKILL 整组；仍不退出则显式报错，不静默吞掉。"""
    _signal_group(pgid, signal.SIGTERM)
    try:
        process.wait(timeout=timeout_sec)
        return
    except subprocess.TimeoutExpired:
        pass
    _signal_group(pgid, signal.SIGKILL)
    try:
        process.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired as exc:
        raise StackError(f"进程组 {pgid} 在 SIGKILL 后仍未退出") from exc


# --------------------------------------------------------------------------------------
# 四单元实例：真实 uvicorn 子进程
# --------------------------------------------------------------------------------------


@dataclass
class ServiceInstance:
    """一个单元的一个真实实例（独立进程组；替换实例即换进程与端口，库/卷/Redis 不变）。"""

    unit: str
    index: int
    module: str
    port: int
    env: Mapping[str, str]
    log_path: Path
    skill_cache_root: Path
    process: subprocess.Popen[bytes] | None = field(default=None, init=False)
    pgid: int | None = field(default=None, init=False)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def pid(self) -> int:
        if self.process is None:
            raise StackError(f"{self.unit}#{self.index} 尚未启动")
        return self.process.pid

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, *, ready_timeout_sec: float = READY_TIMEOUT_SEC) -> None:
        if self.process is not None:
            raise StackError(f"{self.unit}#{self.index} 已启动过：替换请用 replace_instance")
        self.process, self.pgid = _spawn_child(
            [
                sys.executable,
                "-m",
                "uvicorn",
                f"{self.module}:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "error",
            ],
            env=self.env,
            log_path=self.log_path,
        )
        _wait_for_http(
            f"{self.url}{HEALTH_PATH}",
            timeout_sec=ready_timeout_sec,
            process=self.process,
            label=f"{self.unit}#{self.index}",
            log_tail=self.log_tail,
        )

    def command_line(self) -> str:
        return process_command_line(self.pid)

    def log_tail(self, limit: int = 2000) -> str:
        if not self.log_path.exists():
            return ""
        return self.log_path.read_text(encoding="utf-8", errors="replace")[-limit:]

    def stop(self, *, timeout_sec: float = STOP_TIMEOUT_SEC) -> None:
        if self.process is None or self.pgid is None:
            return
        _stop_child(self.process, self.pgid, timeout_sec=timeout_sec)

    def kill(self, *, timeout_sec: float = STOP_TIMEOUT_SEC) -> None:
        """模拟 Pod 崩溃：SIGKILL 整组，等进程与整组都消失。"""
        if self.process is None or self.pgid is None:
            return
        _signal_group(self.pgid, signal.SIGKILL)
        try:
            self.process.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired as exc:
            raise StackError(f"{self.unit}#{self.index} 在 SIGKILL 后仍未退出") from exc


@dataclass
class ProbeProcess:
    """TASK-106 的真实 HTTP 模型探针子进程（真实 socket + SSE 分帧 + 控制面）。"""

    port: int
    log_path: Path
    process: subprocess.Popen[bytes] | None = field(default=None, init=False)
    pgid: int | None = field(default=None, init=False)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def pid(self) -> int:
        if self.process is None:
            raise StackError("模型探针尚未启动")
        return self.process.pid

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, *, timeout_sec: float = READY_TIMEOUT_SEC) -> None:
        self.process, self.pgid = _spawn_child(
            [
                sys.executable,
                "-m",
                "tests.acceptance.capacity.probes",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
            log_path=self.log_path,
        )
        _wait_for_http(
            f"{self.url}{PROBE_STATE_PATH}",
            timeout_sec=timeout_sec,
            process=self.process,
            label="模型探针",
            log_tail=lambda: self.log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            if self.log_path.exists()
            else "",
        )

    def stop(self, *, timeout_sec: float = STOP_TIMEOUT_SEC) -> None:
        if self.process is None or self.pgid is None:
            return
        _stop_child(self.process, self.pgid, timeout_sec=timeout_sec)

    def kill(self, *, timeout_sec: float = STOP_TIMEOUT_SEC) -> None:
        if self.process is None or self.pgid is None:
            return
        _signal_group(self.pgid, signal.SIGKILL)
        try:
            self.process.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired as exc:
            raise StackError("模型探针在 SIGKILL 后仍未退出") from exc


# --------------------------------------------------------------------------------------
# 入口代理：按真实 ingress 清单分流的反向代理子进程
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IngressRoute:
    path: str
    unit: str


def load_ingress_routes(manifest: Path = INGRESS_MANIFEST) -> tuple[IngressRoute, ...]:
    """从真实 ingress 清单生成分流表（最长前缀优先，与 ingress-nginx 同口径）。"""
    try:
        documents = list(yaml.safe_load_all(manifest.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError) as exc:
        raise StackError(f"无法读取入口分流清单 {manifest}：{exc}") from exc
    routes: list[IngressRoute] = []
    for document in documents:
        if not isinstance(document, Mapping):
            continue
        for rule in (document.get("spec") or {}).get("rules") or []:
            for entry in ((rule.get("http") or {}).get("paths") or []):
                routes.append(_ingress_route(entry, manifest))
    if not routes:
        raise StackError(f"{manifest} 里没有可用的入口分流规则")
    return tuple(sorted(routes, key=lambda route: (-len(route.path), route.path)))


def _ingress_route(entry: Mapping[str, Any], manifest: Path) -> IngressRoute:
    if entry.get("pathType") != "Prefix":
        raise StackError(f"{manifest} 含非 Prefix 的 pathType：{entry.get('pathType')!r}")
    service = ((entry.get("backend") or {}).get("service") or {}).get("name")
    unit = INGRESS_SERVICE_UNITS.get(service)
    if unit is None:
        raise StackError(f"{manifest} 的后端服务 {service!r} 不在本栈四单元内")
    return IngressRoute(path=str(entry["path"]), unit=unit)


def _route_matches(route_path: str, request_path: str) -> bool:
    if route_path == "/":
        return True
    return request_path == route_path or request_path.startswith(route_path + "/")


class _StatelessCookieJar(CookieJar):
    """反向代理的 Cookie 语义：**无状态**。

    `httpx.Client` 自带 Cookie jar：上游的 `Set-Cookie` 会被记住，并在**后续任意请求**上回放
    （实测：ADMIN 登录一次后，另一个浏览器上下文不带任何 Cookie 的请求也被当成已登录）。
    真实反向代理不做这件事 —— 凭据只属于请求方，代理既不落盘也不跨请求重放。
    """

    def extract_cookies(self, response: Any, request: Any) -> None:  # noqa: N802 - CookieJar 接口
        return None



class _ProxyServer(ThreadingHTTPServer):
    """真实反向代理：转发字节流（含 SSE 分片），控制面只暴露路由与计数。"""

    daemon_threads = True
    allow_reuse_address = True
    # 默认 listen backlog=5：100 用户同时建立连接时会在内核 accept 队列里排队，
    # 表现为"网关服务本身很快、客户端准入却多出 1s"的假瓶颈（入口代理不是被测单元）。
    request_queue_size = 256

    def __init__(
        self, address: tuple[str, int], backends: Mapping[str, str], routes: Sequence[IngressRoute]
    ) -> None:
        super().__init__(address, _ProxyHandler)
        self.backends = dict(backends)
        self.routes = tuple(routes)
        # 连接上限决定入口代理能同时承载多少条长活 SSE + API：httpx 默认 100 条，
        # 50 用户档就贴顶（50 SSE + 并发 API → 请求在池里无限等）。显式放大并保留 keepalive。
        self.client = httpx.Client(
            timeout=None,
            trust_env=False,
            cookies=_StatelessCookieJar(),
            limits=httpx.Limits(max_connections=500, max_keepalive_connections=200),
        )

        self._lock = threading.Lock()
        self._requests: dict[str, int] = {unit: 0 for unit in self.backends}
        self._failures: dict[str, int] = {unit: 0 for unit in self.backends}

    def route_for(self, path: str) -> str | None:
        matched = [route for route in self.routes if _route_matches(route.path, path)]
        if not matched:
            return None
        return max(matched, key=lambda route: len(route.path)).unit

    def record(self, unit: str) -> None:
        with self._lock:
            self._requests[unit] += 1

    def record_failure(self, unit: str) -> None:
        with self._lock:
            self._failures[unit] += 1

    def state(self) -> dict[str, Any]:
        with self._lock:
            requests, failures = dict(self._requests), dict(self._failures)
        return {
            "routes": [{"path": route.path, "unit": route.unit} for route in self.routes],
            "requests": requests,
            "upstream_failures": failures,
        }

    def shutdown_cleanly(self) -> None:
        self.client.close()
        self.server_close()


class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "muad-capacity-entry-proxy/1.0"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 接口
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._dispatch("OPTIONS")

    def log_message(self, message_format: str, *args: Any) -> None:
        print(f"[entry-proxy] {message_format % args}", flush=True)

    def _dispatch(self, method: str) -> None:
        server = cast("_ProxyServer", self.server)
        if self.path.split("?", 1)[0] == PROXY_STATE_PATH:
            self._send_json(200, server.state())
            return
        unit = server.route_for(self.path)
        if unit is None:
            self._send_json(404, {"error": "no_route", "path": self.path})
            return
        self._forward(method, unit)

    def _read_body(self) -> bytes | None:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return None
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise StackError(f"非法 Content-Length：{raw_length!r}") from exc
        return self.rfile.read(length)

    def _forward(self, method: str, unit: str) -> None:
        import time as _time

        started = _time.monotonic()
        server = cast("_ProxyServer", self.server)
        headers = {
            key: value for key, value in self.headers.items() if key.lower() not in _HOP_BY_HOP
        }
        # SSE 以关闭界定；JSON API 复用上游 keep-alive（真实 ingress 同口径）。
        is_stream = "/events" in self.path or self.headers.get("Accept") == "text/event-stream"
        headers["Connection"] = "close" if is_stream else "keep-alive"
        # 反向代理不持 Cookie 状态：共享 httpx.Client 默认带 cookie jar，会把上一个客户端
        # 登录响应里的 Set-Cookie 悄悄回放到下一个客户端（观测：浏览器无 Cookie 也能拿到
        # 别人的 `/webchat/auth/me` 200）。真实代理只转发**本次请求**携带的 Cookie。
        server.client.cookies.clear()
        try:
            body = self._read_body()
            with server.client.stream(
                method, f"{server.backends[unit]}{self.path}", headers=headers, content=body
            ) as upstream:
                server.record(unit)
                self._relay(upstream, head_only=method == "HEAD")
        except StackError as exc:
            self._send_json(400, {"error": "bad_request", "reason": str(exc)})
        except httpx.HTTPError as exc:
            server.record_failure(unit)
            self._send_json(
                502, {"error": "upstream_unavailable", "unit": unit, "reason": type(exc).__name__}
            )
        finally:
            elapsed_ms = (_time.monotonic() - started) * 1000
            if elapsed_ms > 500:
                print(
                    f"[entry-proxy] slow {method} {self.path} unit={unit} "
                    f"elapsed_ms={elapsed_ms:.0f}",
                    flush=True,
                )

    def _relay(self, upstream: httpx.Response, *, head_only: bool) -> None:
        # 真实 ingress 复用客户端连接；只有无 Content-Length 的响应（SSE/错误体）才以关闭界定。
        # 此前无条件 `Connection: close` 让 100 用户每发一条消息都重建连接，准入被连接建立拖慢。
        has_length = "content-length" in upstream.headers
        self.close_connection = not has_length
        self.send_response(upstream.status_code)
        # `multi_items()`：重复头必须逐条透传。`items()` 会把同名头合并成一条逗号连接的值，
        # 对 `Set-Cookie` 直接违反 RFC 6265（Cookie 头不允许合并）——登录响应同时下发
        # `muad_session`/`muad_csrf`，合并后浏览器只能存下第一个，CSRF 双提交面全断
        # （nginx 等真实入口按多条透传，本代理必须同口径）。

        for key, value in upstream.headers.multi_items():
            if key.lower() in _HOP_BY_HOP:
                continue
            if key.lower() == "content-length" and not has_length:
                continue
            self.send_header(key, value)
        self.send_header("Connection", "keep-alive" if has_length else "close")
        self.end_headers()
        if head_only:
            return
        for chunk in upstream.iter_raw():
            self.wfile.write(chunk)
            self.wfile.flush()

    def _send_json(self, status: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = False


@dataclass
class EntryProxy:
    """入口代理子进程：起停即真实进程起停，替换/崩溃按整进程组处理。"""

    port: int
    log_path: Path
    backends: Mapping[str, str]
    process: subprocess.Popen[bytes] | None = field(default=None, init=False)
    pgid: int | None = field(default=None, init=False)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def pid(self) -> int:
        if self.process is None:
            raise StackError("入口代理尚未启动")
        return self.process.pid

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, *, timeout_sec: float = READY_TIMEOUT_SEC) -> None:
        argv = [
            sys.executable,
            "-m",
            "tests.acceptance.capacity.stack",
            "proxy",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
        ]
        for unit, url in self.backends.items():
            argv += ["--backend", f"{unit}={url}"]
        self.process, self.pgid = _spawn_child(
            argv, env={**os.environ, "PYTHONPATH": str(REPO_ROOT)}, log_path=self.log_path
        )
        _wait_for_http(
            f"{self.url}{PROXY_STATE_PATH}",
            timeout_sec=timeout_sec,
            process=self.process,
            label="入口代理",
            log_tail=lambda: self.log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            if self.log_path.exists()
            else "",
        )

    def stats(self) -> dict[str, Any]:
        response = _probe_http(f"{self.url}{PROXY_STATE_PATH}", timeout_sec=10.0)
        if response is None or response.status_code != 200:
            raise StackError(f"入口代理控制面不可用（{self.url}{PROXY_STATE_PATH}）")
        return cast("dict[str, Any]", response.json())

    def stop(self, *, timeout_sec: float = STOP_TIMEOUT_SEC) -> None:
        if self.process is None or self.pgid is None:
            return
        _stop_child(self.process, self.pgid, timeout_sec=timeout_sec)

    def kill(self, *, timeout_sec: float = STOP_TIMEOUT_SEC) -> None:
        if self.process is None or self.pgid is None:
            return
        _signal_group(self.pgid, signal.SIGKILL)
        try:
            self.process.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired as exc:
            raise StackError("入口代理在 SIGKILL 后仍未退出") from exc


# --------------------------------------------------------------------------------------
# 四单元独立验收栈
# --------------------------------------------------------------------------------------


class CapacityStack:
    """四单元独立验收栈：本轮库/Redis + 真实进程/共享卷/入口代理 + 故障进程控制。"""

    def __init__(
        self,
        *,
        root: Path,
        profile: CapacityProfile | None = None,
        datastore: DatastoreHandle | None = None,
        service_env: Mapping[str, Mapping[str, str]] | None = None,
        ready_timeout_sec: float = READY_TIMEOUT_SEC,
    ) -> None:
        self.stack_id = f"capacity-{uuid.uuid4().hex[:8]}"
        self.root = Path(root)
        self.profile = profile
        self.replicas = self._resolve_replicas(profile)
        self.tenant_id = f"capacity-{uuid.uuid4().hex[:8]}"
        self.internal_token = f"capacity-internal-{uuid.uuid4().hex}"
        self._datastore = datastore
        self._service_env = {unit: dict(values) for unit, values in (service_env or {}).items()}
        self._ready_timeout_sec = ready_timeout_sec
        self._instances: dict[str, list[ServiceInstance]] = {unit: [] for unit in UNITS}
        self._ports: dict[str, list[int]] = {unit: [] for unit in UNITS}
        self._proxy: EntryProxy | None = None
        self._probe: ProbeProcess | None = None
        self._started = False
        self._torn_down = False

    @staticmethod
    def _resolve_replicas(profile: CapacityProfile | None) -> dict[str, int]:
        """实例数 = profile 的 replica_counts；超 max_replica_counts 在启动前显式拒绝。"""
        if profile is None or profile.resources is None:
            return {unit: 1 for unit in UNITS}
        resources = profile.resources
        replicas: dict[str, int] = {}
        for unit in UNITS:
            wanted, maximum = resources.replica_counts[unit], resources.max_replica_counts[unit]
            if wanted > maximum:
                raise ValueError(
                    f"{unit} 的 replica_counts={wanted} 超过 max_replica_counts={maximum}："
                    "容量栈按声明启动，不静默截断"
                )
            replicas[unit] = wanted
        return replicas

    @property
    def datastore(self) -> DatastoreHandle:
        if self._datastore is None:
            raise StackError("数据存储尚未取得：先调用 start()")
        return self._datastore

    @property
    def database_url(self) -> str:
        return self.datastore.database_url

    @property
    def redis_url(self) -> str:
        return self.datastore.redis_url

    @property
    def shared_root(self) -> Path:
        return self.root / "shared"

    @property
    def artifacts_root(self) -> Path:
        """四单元同挂的 RWX 共享卷（真实目录）。"""
        return self.shared_root / "artifacts"

    def skill_cache_root(self, unit: str, index: int) -> Path:
        """每进程独立的 Skill cache root（设计 §4.1：不共享缓存根）。"""
        return self.root / "skill-cache" / f"{unit}-{index}"

    @property
    def started(self) -> bool:
        return self._started

    @property
    def proxy(self) -> EntryProxy:
        if self._proxy is None:
            raise StackError("入口代理尚未启动")
        return self._proxy

    @property
    def model_probe(self) -> ProbeProcess:
        if self._probe is None:
            raise StackError("模型探针尚未启动")
        return self._probe

    def start(self) -> None:
        """取得数据存储 → 建共享卷 → 起探针 → 按 Console→Runtime→Worker→Gateway 起四单元 → 起代理。"""
        if self._started:
            raise StackError("栈已启动：重复 start 不重建（替换实例用 replace_instance）")
        self._datastore = self._datastore or acquire_datastore()
        self.artifacts_root.mkdir(parents=True, exist_ok=True)
        for unit in UNITS:
            for index in range(self.replicas[unit]):
                self.skill_cache_root(unit, index).mkdir(parents=True, exist_ok=True)
        self._ports = {unit: [free_port() for _ in range(self.replicas[unit])] for unit in UNITS}
        self._probe = ProbeProcess(port=free_port(), log_path=self.root / "logs" / "model-probe.log")
        self._probe.start(timeout_sec=self._ready_timeout_sec)
        for unit in UNITS:  # Console 先起：Gateway 快照、Worker 结果回流都指向它
            for index in range(self.replicas[unit]):
                self.start_instance(unit, index=index)
        self.start_proxy()
        self._started = True

    def all_instances(self) -> tuple[ServiceInstance, ...]:
        return tuple(
            instance for unit in UNITS for instance in self._instances[unit]
        )

    def instances(self, unit: str) -> tuple[ServiceInstance, ...]:
        self._require_unit(unit)
        return tuple(self._instances[unit])

    def instance(self, unit: str, index: int = 0) -> ServiceInstance:
        self._require_unit(unit)
        try:
            return self._instances[unit][index]
        except IndexError as exc:
            raise StackError(f"{unit} 没有 #{index} 号实例（现有 {len(self._instances[unit])} 个）") from exc

    def url_of(self, unit: str, index: int = 0) -> str:
        """单元地址：已启动实例用真实端口，尚未启动的实例用启动前规划端口。"""
        self._require_unit(unit)
        if index < len(self._instances[unit]):
            return self._instances[unit][index].url
        try:
            return f"http://127.0.0.1:{self._ports[unit][index]}"
        except IndexError as exc:
            raise StackError(f"{unit} 没有 #{index} 号实例的端口规划") from exc

    def start_instance(self, unit: str, *, index: int | None = None) -> ServiceInstance:
        """起一个真实实例；显式 index 时替换该槽位（旧实例必须已停止）。"""
        self._require_unit(unit)
        if index is None:
            index = len(self._instances[unit])
        existing = self._instances[unit][index] if index < len(self._instances[unit]) else None
        if existing is not None and existing.alive:
            raise StackError(f"{unit}#{index} 仍在运行：先 stop_instance/kill_instance 再替换")
        # 初始实例用**启动前规划的端口**：其它单元在它启动之前就把该端口写进了自己的 env
        # （`_unit_urls`），换端口会让"先起的单元指向不存在的地址"；替换实例另取空闲端口。
        port = free_port() if existing is not None else self._planned_port(unit, index)
        instance = ServiceInstance(
            unit=unit,
            index=index,
            module=UNIT_MODULES[unit],
            port=port,
            env=self._service_env_for(unit, index, port),
            log_path=self.root / "logs" / f"{unit}-{index}.log",
            skill_cache_root=self.skill_cache_root(unit, index),
        )
        instance.start(ready_timeout_sec=self._ready_timeout_sec)
        if index < len(self._ports[unit]):
            self._ports[unit][index] = port
        else:
            self._ports[unit].append(port)
        if existing is None:
            self._instances[unit].append(instance)
        else:
            self._instances[unit][index] = instance
        return instance

    def _planned_port(self, unit: str, index: int) -> int:
        """启动前为该槽位规划的端口（`start()` 已分配；独立起实例时按需补一个）。"""
        planned = self._ports[unit]
        while len(planned) <= index:
            planned.append(free_port())
        return planned[index]

    def stop_instance(self, instance: ServiceInstance, *, timeout_sec: float = STOP_TIMEOUT_SEC) -> None:
        instance.stop(timeout_sec=timeout_sec)

    def kill_instance(self, instance: ServiceInstance, *, timeout_sec: float = STOP_TIMEOUT_SEC) -> None:
        instance.kill(timeout_sec=timeout_sec)

    def replace_instance(self, unit: str, index: int = 0) -> ServiceInstance:
        """故障替换：停掉当前实例，起一个新实例（新端口，同库/卷/Redis）。"""
        current = self.instance(unit, index)
        current.stop()
        return self.start_instance(unit, index=index)

    def restart_instance(
        self, unit: str, index: int = 0, *, timeout_sec: float = STOP_TIMEOUT_SEC
    ) -> ServiceInstance:
        """原地重启（同端口/同地址的新进程）：单 Pod 重启口径。

        与 `replace_instance` 的区别是**地址不变**：其余单元与入口代理在启动时就把该单元的
        地址写进了自己的 env/后端表（`_unit_urls`、`start_proxy`），换端口会让它们指向死地址。
        单 Pod 重启（Service 地址稳定、Pod 进程换新）必须保持同一地址；替换实例（新端口）只用于
        不被其它单元引用的槽位。停机用整进程组（SIGTERM→SIGKILL），启动仍走真实 uvicorn 子进程。
        """
        current = self.instance(unit, index)
        port = current.port
        log_path = current.log_path
        skill_cache_root = current.skill_cache_root
        current.stop(timeout_sec=timeout_sec)
        instance = ServiceInstance(
            unit=unit,
            index=index,
            module=UNIT_MODULES[unit],
            port=port,
            env=self._service_env_for(unit, index, port),
            log_path=log_path,
            skill_cache_root=skill_cache_root,
        )
        instance.start(ready_timeout_sec=self._ready_timeout_sec)
        self._instances[unit][index] = instance
        return instance

    def start_proxy(self) -> EntryProxy:
        """起（或重启）入口代理：分流表读真实 ingress 清单，后端地址取当前实例。"""
        if self._proxy is not None and self._proxy.alive:
            raise StackError("入口代理已在运行")
        proxy = EntryProxy(
            port=free_port(),
            log_path=self.root / "logs" / "entry-proxy.log",
            backends={
                "console-platform": self.url_of("console-platform"),
                "im-gateway": self.url_of("im-gateway"),
            },
        )
        proxy.start(timeout_sec=self._ready_timeout_sec)
        self._proxy = proxy
        return proxy

    def stop_proxy(self, *, timeout_sec: float = STOP_TIMEOUT_SEC) -> None:
        if self._proxy is None:
            return
        self._proxy.stop(timeout_sec=timeout_sec)

    def write_shared_file(self, relative_path: str, content: bytes) -> Path:
        """往共享卷写真实文件（附件/制品同卷口径）。"""
        target = self.artifacts_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def read_shared_file(self, relative_path: str) -> bytes:
        return (self.artifacts_root / relative_path).read_bytes()

    def summary(self) -> dict[str, Any]:
        """可观测快照（报告用）：进程/端口/卷/库，不回带任何凭据原文。"""
        return {
            "stack_id": self.stack_id,
            "datastore": {
                "name": self.datastore.name,
                "owns": self.datastore.owns,
                "redis_db": _redis_db_index(self.redis_url),
            },
            "artifacts_root": str(self.artifacts_root),
            "instances": [
                {
                    "unit": instance.unit,
                    "index": instance.index,
                    "pid": instance.pid,
                    "pgid": instance.pgid,
                    "port": instance.port,
                    "alive": instance.alive,
                }
                for instance in self.all_instances()
            ],
            "proxy": None
            if self._proxy is None
            else {"pid": self._proxy.pid, "port": self._proxy.port, "alive": self._proxy.alive},
            "model_probe": None
            if self._probe is None
            else {"pid": self._probe.pid, "port": self._probe.port, "alive": self._probe.alive},
        }

    def teardown(self) -> None:
        """收尾只清本轮：停代理/探针/全部实例（整组）→ drop 本轮库并释放号位 → 删本轮目录。"""
        if self._torn_down:
            return
        self._torn_down = True
        if self._proxy is not None:
            self._proxy.stop()
        for instance in reversed(self.all_instances()):
            instance.stop()
        if self._probe is not None:
            self._probe.stop()
        self._instances = {unit: [] for unit in UNITS}
        if self._datastore is not None and self._datastore.owns:
            drop_datastore(
                self._datastore.name,
                self._datastore.admin_database_url,
                self._datastore.redis_url,
            )
        if self.root.exists():
            shutil.rmtree(self.root)
        self._started = False

    def _require_unit(self, unit: str) -> None:
        if unit not in UNITS:
            raise StackError(f"未知单元 {unit!r}：本栈只有 {', '.join(UNITS)}")

    def _unit_urls(self, unit: str, port: int) -> dict[str, str]:
        """四单元互指的地址；当前实例用它的真实端口，其余单元取 0 号实例。"""
        urls = {
            "CONSOLE_PLATFORM_URL": self.url_of("console-platform"),
            "AGENT_RUNTIME_URL": self.url_of("agent-runtime"),
            "AGENT_WORKER_URL": self.url_of("agent-worker"),
            "IM_GATEWAY_URL": self.url_of("im-gateway"),
        }
        env_key = {
            "console-platform": "CONSOLE_PLATFORM_URL",
            "agent-runtime": "AGENT_RUNTIME_URL",
            "agent-worker": "AGENT_WORKER_URL",
            "im-gateway": "IM_GATEWAY_URL",
        }[unit]
        urls[env_key] = f"http://127.0.0.1:{port}"
        return urls

    def _service_env_for(self, unit: str, index: int, port: int) -> dict[str, str]:
        env = {
            **os.environ,
            "DATABASE_URL": self.database_url,
            "REDIS_URL": self.redis_url,
            "ARTIFACT_ROOT": str(self.artifacts_root),
            "SKILL_CACHE_ROOT": str(self.skill_cache_root(unit, index)),
            "DEFAULT_TENANT_ID": self.tenant_id,
            "INTERNAL_SERVICE_TOKEN": self.internal_token,
            "MIGRATIONS_DIR": str(REPO_ROOT / "migrations" / "versions"),
            "PYTHONPATH": str(REPO_ROOT),
            STACK_ID_ENV: self.stack_id,
        }
        env.update(self._unit_urls(unit, port))
        env.update(self._service_env.get(unit, {}))
        return env


# --------------------------------------------------------------------------------------
# 子进程模式：入口代理
# --------------------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tests.acceptance.capacity.stack")
    subparsers = parser.add_subparsers(dest="command", required=True)
    proxy = subparsers.add_parser("proxy", help="run the entry proxy in the foreground")
    proxy.add_argument("--host", default="127.0.0.1")
    proxy.add_argument("--port", type=int, default=0)
    proxy.add_argument(
        "--backend",
        action="append",
        default=[],
        metavar="UNIT=URL",
        help="单元名到后端地址（可重复；分流表由 deploy/k8s/base/ingress.yaml 决定）",
    )
    proxy.set_defaults(handler=_run_proxy)
    return parser


def _parse_backends(entries: Sequence[str]) -> dict[str, str]:
    backends: dict[str, str] = {}
    for entry in entries:
        unit, separator, url = entry.partition("=")
        if not separator or unit not in UNITS or not url:
            raise SystemExit(f"非法 --backend {entry!r}：应为 UNIT=URL（UNIT ∈ {', '.join(UNITS)}）")
        backends[unit] = url
    return backends


def _run_proxy(args: argparse.Namespace) -> int:
    backends = _parse_backends(args.backend)
    routes = load_ingress_routes()
    missing = sorted({route.unit for route in routes} - set(backends))
    if missing:
        raise SystemExit(f"入口分流缺少后端地址：{', '.join(missing)}")
    server = _ProxyServer((args.host, args.port), backends, routes)
    print(json.dumps({"url": f"http://{args.host}:{server.server_address[1]}"}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown_cleanly()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
