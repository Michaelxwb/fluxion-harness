"""[B-299] Worker/Console 资源与认证观测：指标目录、归属、真实打点与四单元独立读取。

design §3.5 质量实现方案 / §4.1 部署与容量；TASK-099。

不得 Mock 的真实边界：
- 真实源码依赖图：两个单元指标模块的 CATALOG 与调用点的 import/调用关系（AST 直接读真实源码）；
- 真实 api-kit 进程内注册表 → 真实 `GET /metrics`（四单元各自在**独立子进程**里真实 ASGI 渲染，
  证明四个 catalog 互不依赖、可独立读取）；
- 真实 HTTP 协议探针（本机真实 socket：200/401/500/拒连/挂起/慢滴），不访问共享 dev 数据；
- 真实维护循环失败（连真实拒连端口）与真实 logging-kit 日志出口（tenant/trace 关联 + canary 凭据）。

覆盖 B-299 四条关键断言：
1. 任务回收/投递归属正确（Worker 目录覆盖 reclaim/lease/delivery/maintenance，循环打点归属正确）；
2. 认证/探针/池等待与配置失败可区分（Console 目录 + 真实样本各自独立家族）；
3. 标准日志有 tenant/trace 关联但不含 Token/Key；
4. 四单元 catalog 可独立读取（独立进程各自渲染完整目录，互不泄漏）。
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import socket
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from importlib.util import resolve_name
from pathlib import Path
from typing import Any, get_args

import httpx
import pytest
from fastapi import FastAPI, Request
from muad_agent_worker import metrics as worker_metrics
from muad_agent_worker.worker.service import WorkerLoop
from muad_api import AppError, ErrorCode, render_metrics
from muad_api.context import context_scope
from muad_console_platform import metrics as console_metrics
from muad_console_platform.api.deps import get_probe_capacity_client
from muad_console_platform.application.model_probe_service import (
    ProbeCapacityClient,
    probe_model_endpoint,
)
from muad_console_platform.infrastructure.models.control import ModelCredential
from muad_logging import configure_logging
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]

WORKER_METRICS_SOURCE = ROOT / "apps/agent-worker/src/muad_agent_worker/metrics.py"
WORKER_SERVICE_SOURCE = ROOT / "apps/agent-worker/src/muad_agent_worker/worker/service.py"
DELIVERY_SERVICE_SOURCE = ROOT / "apps/agent-worker/src/muad_agent_worker/delivery/service.py"
CONSOLE_METRICS_SOURCE = ROOT / "apps/console-platform/backend/src/muad_console_platform/metrics.py"
CONSOLE_AUTH_SOURCE = ROOT / "apps/console-platform/backend/src/muad_console_platform/api/webchat_auth.py"
CONSOLE_PROBE_SOURCE = (
    ROOT / "apps/console-platform/backend/src/muad_console_platform/application/model_probe_service.py"
)
CONSOLE_MODEL_TEST_SOURCE = (
    ROOT / "apps/console-platform/backend/src/muad_console_platform/application/model_test_service.py"
)
CONSOLE_DEPS_SOURCE = ROOT / "apps/console-platform/backend/src/muad_console_platform/api/deps.py"
CONSOLE_MAIN_SOURCE = ROOT / "apps/console-platform/backend/src/muad_console_platform/main.py"
CONSOLE_HTTP_SOURCE = (
    ROOT / "apps/console-platform/backend/src/muad_console_platform/infrastructure/http_clients.py"
)

#: 四部署单元：各自在独立进程里安装自己的目录并渲染 `/metrics`（互不 import、互不泄漏）。
UNIT_CATALOGS: tuple[tuple[str, str, str], ...] = (
    ("worker", "muad_agent_worker.metrics", "install_worker_metrics"),
    ("console", "muad_console_platform.metrics", "install_console_metrics"),
    ("runtime", "muad_agent_runtime.metrics", "install_runtime_metrics"),
    ("gateway", "muad_im_gateway.metrics", "install_gateway_metrics"),
)
#: 每个单元独有、绝不该出现在别的单元 `/metrics` 里的指标（catalog 独立读取的直接证据）。
FOREIGN_METRICS: dict[str, tuple[str, ...]] = {
    "worker": ("chat_auth_total", "model_probe_total", "console_config_failure_total"),
    "console": ("task_reclaim_total", "worker_maintenance_total", "delivery_total"),
    "runtime": ("chat_auth_total", "worker_maintenance_total"),
    "gateway": ("chat_auth_total", "worker_maintenance_total"),
}

#: Worker/Console 目录允许的 label 名：固定低基数枚举，不得混入租户/用户/资源 ID/凭据。
ALLOWED_LABEL_NAMES = frozenset(
    {"type", "status", "caller", "result", "operation", "reason", "component", "pool"}
)
SENSITIVE_LABEL_MARKERS = (
    "authorization",
    "api_key",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "password",
    "cookie",
    "credential",
)
HIGH_CARDINALITY_LABEL_NAMES = frozenset(
    {
        "tenant",
        "tenant_id",
        "user",
        "user_id",
        "run_id",
        "conversation_id",
        "agent_id",
        "model_id",
        "credential_id",
        "session_id",
        "task_id",
        "schedule_id",
        "trace_id",
        "request_id",
        "key",
    }
)

CANARY_API_KEY = "sk-b299-canary-key-material-0001"
CANARY_SERVICE_TOKEN = "b299-internal-service-token-canary"

SAMPLE_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?P<labels>\{.*\})?\s+(?P<value>[0-9eE+\-.]+)$"
)

UNIT_CATALOG_SCRIPT = """
import asyncio, importlib, json, sys
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

module = importlib.import_module(sys.argv[1])
install = getattr(module, sys.argv[2])


async def main() -> None:
    app = FastAPI()
    install(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://unit") as client:
        response = await client.get("/metrics")
    names = sorted(
        {line.split()[2] for line in response.text.splitlines() if line.startswith("# TYPE ")}
    )
    declared = sorted({entry[0] for entry in module.CATALOG})
    print(json.dumps({"names": names, "declared": declared}))


asyncio.run(main())
"""


# --- 通用解析与 AST 工具 ----------------------------------------------------------------


def _parse_samples(text: str) -> list[tuple[str, dict[str, str], float]]:
    samples: list[tuple[str, dict[str, str], float]] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        matched = SAMPLE_PATTERN.match(line.strip())
        if matched is None:
            continue
        labels: dict[str, str] = {}
        raw_labels = matched.group("labels")
        if raw_labels:
            for pair in raw_labels[1:-1].split(","):
                name, _, value = pair.partition("=")
                labels[name.strip()] = value.strip().strip('"')
        samples.append((matched.group("name"), labels, float(matched.group("value"))))
    return samples


def _sample_value(text: str, name: str, labels: dict[str, str]) -> float:
    for sample_name, sample_labels, value in _parse_samples(text):
        if sample_name == name and all(sample_labels.get(key) == item for key, item in labels.items()):
            return value
    return 0.0


def _delta(after: str, before: str, name: str, labels: dict[str, str]) -> float:
    return _sample_value(after, name, labels) - _sample_value(before, name, labels)


def _catalog_map(module: Any) -> dict[str, tuple[str, tuple[str, ...]]]:
    return {name: (kind, labels) for name, kind, labels, _help in module.CATALOG}


def _parse_source(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _source_root(path: Path) -> Path:
    parts = path.parts
    return Path(*parts[: parts.index("src") + 1])


def _imports(path: Path) -> set[str]:
    """按真实源码解析 import（相对导入解析成绝对模块名，同 test_runtime_attachments 口径）。"""
    root = _source_root(path)
    package = ".".join(path.relative_to(root).parts[:-1])
    imported: set[str] = set()
    for node in ast.walk(_parse_source(path)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            name = "." * node.level + (node.module or "")
            imported.add(resolve_name(name, package) if node.level else name)
    return imported


def _callee_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _function(node: ast.Module | ast.ClassDef, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
            return item
    raise AssertionError(f"源码缺少函数：{name}")


def _class_method(
    tree: ast.Module, class_name: str, method_name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return _function(node, method_name)
    raise AssertionError(f"源码缺少类：{class_name}")


def _calls(node: ast.AST, callee: str) -> list[ast.Call]:
    return [
        item
        for item in ast.walk(node)
        if isinstance(item, ast.Call) and _callee_name(item.func) == callee
    ]


def _string_args(call: ast.Call) -> list[str]:
    return [
        str(argument.value)
        for argument in call.args
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
    ]


def _first_arg_name(call: ast.Call) -> str:
    if call.args and isinstance(call.args[0], ast.Name):
        return call.args[0].id
    return ""


def _keyword_literals(calls: list[ast.Call], keyword: str) -> set[str]:
    return {
        str(item.value.value)
        for call in calls
        for item in call.keywords
        if item.arg == keyword and isinstance(item.value, ast.Constant)
    }


def _is_metric_name(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _count_outcome_operations(tree: ast.Module, function_name: str, metric_name: str) -> set[str]:
    """收集函数体内 `with count_outcome(<metric>, {"operation": ...})` 的字面 operation 值。"""
    operations: set[str] = set()
    for node in ast.walk(_function(tree, function_name)):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            call = item.context_expr
            if not isinstance(call, ast.Call) or _callee_name(call.func) != "count_outcome":
                continue
            if not call.args or not _is_metric_name(call.args[0], metric_name):
                continue
            labels = call.args[1] if len(call.args) > 1 else None
            if not isinstance(labels, ast.Dict):
                continue
            for key, value in zip(labels.keys, labels.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "operation"
                    and isinstance(value, ast.Constant)
                ):
                    operations.add(str(value.value))
    return operations


def _recorded_statuses(function: ast.FunctionDef | ast.AsyncFunctionDef, metric_name: str) -> set[str]:
    """函数体内 `record_outcome(<metric>, "<status>")` 的字面 status 值。"""
    statuses: set[str] = set()
    for call in _calls(function, "record_outcome"):
        if not call.args or not _is_metric_name(call.args[0], metric_name):
            continue
        if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
            statuses.add(str(call.args[1].value))
    return statuses


def _render_unit_catalog(module_name: str, install_name: str) -> tuple[set[str], set[str]]:
    """独立子进程：真实安装该单元目录 → 真实 `/metrics` 文本里声明的指标名。"""
    completed = subprocess.run(
        [sys.executable, "-c", UNIT_CATALOG_SCRIPT, module_name, install_name],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, f"{module_name} 独立进程渲染失败：{completed.stderr}"
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    return set(payload["names"]), set(payload["declared"])


# --- 真实 HTTP 探针服务（本机 socket，无共享数据） --------------------------------------


class _ProbeHttpServer:
    """真实 HTTP 响应器：/ok 200、/unauthorized 401、/server-error 500、/hang 挂起。

    `drip_all=True` 时所有请求都按 0.2 秒节奏滴字节：连接一直占着（不触发读超时），
    用于制造真实的**池等待**——后一个请求只能在池里等。
    """

    def __init__(self, *, drip_all: bool = False) -> None:
        self._server: asyncio.AbstractServer | None = None
        self._port = 0
        self._release = asyncio.Event()
        self._drip_all = drip_all

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self._port = int(self._server.sockets[0].getsockname()[1])

    async def stop(self) -> None:
        self._release.set()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        await asyncio.sleep(0)

    def url(self, path: str = "") -> str:
        return f"http://127.0.0.1:{self._port}{path}"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            while True:
                line = await reader.readline()
                if not line or line in (b"\r\n", b"\n"):
                    break
            path = request_line.split(b" ")[1].decode() if request_line else ""
            if self._drip_all or "/drip/" in path:
                await self._drip(writer)
            elif "/hang/" in path:
                await self._release.wait()
                await self._write(writer, 200, b"{}")
            else:
                status, body = self._canned(path)
                await self._write(writer, status, body)
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        finally:
            with suppress(Exception):
                writer.close()

    @staticmethod
    def _canned(path: str) -> tuple[int, bytes]:
        if "/unauthorized/" in path:
            return 401, b'{"error":"unauthorized"}'
        if "/server-error/" in path:
            return 500, b'{"error":"boom"}'
        return 200, b'{"data":[]}'

    @staticmethod
    async def _write(writer: asyncio.StreamWriter, status: int, body: bytes) -> None:
        writer.write(
            f"HTTP/1.1 {status} X\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()

    async def _drip(self, writer: asyncio.StreamWriter) -> None:
        writer.write(b"HTTP/1.1 200 X\r\nContent-Length: 1000000\r\n\r\n")
        await writer.drain()
        while not self._release.is_set():
            writer.write(b"x")
            await writer.drain()
            await asyncio.sleep(0.2)


def _credential(base_url: str, *, api_key: str = CANARY_API_KEY) -> ModelCredential:
    return ModelCredential(
        tenant_id="b299-tenant",
        provider_key="openai",
        key=f"b299-cred-{uuid.uuid4().hex[:8]}",
        base_url=base_url,
        api_key=api_key,
        api_key_digest="0" * 64,
        max_inflight=4,
        enabled=True,
    )


@asynccontextmanager
async def _probe_server(*, drip_all: bool = False) -> Iterator[_ProbeHttpServer]:
    server = _ProbeHttpServer(drip_all=drip_all)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


def _closed_port_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    return f"http://127.0.0.1:{port}"


def _capacity_request() -> Any:
    from muad_contracts.model_pool import ProbeCapacityRequest

    return ProbeCapacityRequest(
        probe_id=uuid.uuid4(),
        model_id=uuid.uuid4(),
        credential_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        reserved_tokens=145,
    )


# --- 1/2. 目录：Worker 回收投递归属；Console 认证/探针/池等待/配置失败分家 -----------------


def test_b299_worker_catalog_covers_reclaim_delivery_and_maintenance() -> None:
    """[B-299] Worker 目录覆盖任务回收/投递归属：reclaim/lease/delivery/维护节拍都在目录里。"""
    catalog = _catalog_map(worker_metrics)
    assert catalog[worker_metrics.TASK_RECLAIM_METRIC] == ("counter", ())
    assert catalog[worker_metrics.TASK_LEASE_EXPIRED_METRIC] == ("counter", ())
    assert catalog[worker_metrics.DELIVERY_METRIC] == ("counter", ("status",))
    assert catalog[worker_metrics.DELIVERY_ATTEMPT_METRIC] == ("counter", ())
    assert catalog[worker_metrics.DELIVERY_FAILED_METRIC] == ("counter", ())
    assert catalog[worker_metrics.DELIVERY_ARTIFACT_GONE_METRIC] == ("counter", ())
    assert catalog[worker_metrics.MAINTENANCE_METRIC] == ("counter", ("status",))


def test_b299_console_catalog_separates_auth_probe_pool_wait_and_config_failure() -> None:
    """[B-299] Console 目录把认证/探针/池等待/配置失败分成四个可区分的家族。"""
    catalog = _catalog_map(console_metrics)
    assert catalog[console_metrics.CHAT_AUTH_METRIC] == ("counter", ("operation", "status"))
    assert catalog[console_metrics.MODEL_PROBE_METRIC] == ("counter", ("status",))
    assert catalog[console_metrics.MODEL_PROBE_FAILURE_METRIC] == ("counter", ("reason",))
    assert catalog[console_metrics.HTTP_POOL_WAIT_METRIC] == ("counter", ("pool",))
    assert catalog[console_metrics.CONFIG_FAILURE_METRIC] == ("counter", ("component", "reason"))
    assert set(get_args(console_metrics.ConsolePool)) == {"control", "model", "batch"}


def test_b299_worker_console_catalogs_use_only_low_cardinality_label_names() -> None:
    """[B-299] 两个目录的 label 名只允许固定低基数枚举：无租户/用户/资源 ID/凭据。"""
    for module in (worker_metrics, console_metrics):
        for name, _kind, labels, _help in module.CATALOG:
            for label in labels:
                assert label in ALLOWED_LABEL_NAMES, f"{name} 的 label {label!r} 不在低基数白名单"
                assert label not in HIGH_CARDINALITY_LABEL_NAMES, f"{name} 的 label {label!r} 高基数"
                for marker in SENSITIVE_LABEL_MARKERS:
                    assert marker not in label.lower(), f"{name} 的 label {label!r} 命中敏感标记"


# --- 4. 四单元 catalog 可独立读取（独立进程，互不依赖） -----------------------------------


@pytest.mark.parametrize(("unit", "module_name", "install_name"), UNIT_CATALOGS)
def test_b299_unit_catalog_renders_independently(unit: str, module_name: str, install_name: str) -> None:
    """[B-299] 每个部署单元在自己的进程里渲染完整目录，且不泄漏其他单元的指标。"""
    names, declared = _render_unit_catalog(module_name, install_name)
    assert declared, f"{unit} 的 CATALOG 为空"
    missing = sorted(declared - names)
    assert not missing, f"{unit} 无流量 `/metrics` 缺目录声明：{missing}"
    leaked = sorted(set(FOREIGN_METRICS[unit]) & names)
    assert not leaked, f"{unit} 的 `/metrics` 泄漏了其他单元的指标：{leaked}"


def test_b299_unit_metrics_modules_do_not_import_each_other() -> None:
    """[B-299] 四个单元的指标模块互不 import：目录独立可读，不靠另一个单元才能启动。"""
    unit_packages = {
        "worker": "muad_agent_worker",
        "console": "muad_console_platform",
        "runtime": "muad_agent_runtime",
        "gateway": "muad_im_gateway",
    }
    sources = {
        "worker": WORKER_METRICS_SOURCE,
        "console": CONSOLE_METRICS_SOURCE,
        "runtime": ROOT / "apps/agent-runtime/src/muad_agent_runtime/metrics.py",
        "gateway": ROOT / "apps/im-gateway/src/muad_im_gateway/metrics.py",
    }
    for unit, path in sources.items():
        own = unit_packages[unit]
        for imported in _imports(path):
            for other in unit_packages.values():
                if other == own:
                    continue
                assert not (imported == other or imported.startswith(other + ".")), (
                    f"{unit} 的 metrics 模块 import 了 {imported}（目录不再独立）"
                )


# --- 1. 回收/投递打点归属（真实源码依赖图） ----------------------------------------------


def test_b299_worker_reclaim_and_delivery_are_attributed_to_worker_loops() -> None:
    """[B-299] 回收由独立维护循环驱动并计数；投递计数属 Worker 目录（不是各写各的名字）。"""
    worker_tree = _parse_source(WORKER_SERVICE_SOURCE)
    maintenance = _class_method(worker_tree, "WorkerLoop", "run_maintenance_forever")
    assert _calls(maintenance, "reclaim_expired"), "维护循环必须驱动回收"
    assert _recorded_statuses(maintenance, "MAINTENANCE_METRIC") == {
        "ok",
        "failed",
    }, "维护节拍的成功/失败都必须计数（失败可见、不静默）"
    reclaim = _class_method(worker_tree, "WorkerLoop", "reclaim_expired")
    reclaim_metric_names = {_first_arg_name(call) for call in _calls(reclaim, "increment")}
    assert reclaim_metric_names == {"TASK_RECLAIM_METRIC", "TASK_LEASE_EXPIRED_METRIC"}

    assert "muad_agent_worker.metrics" in _imports(DELIVERY_SERVICE_SOURCE), (
        "投递计数必须来自 Worker 指标目录（单一口径）"
    )
    delivery_tree = _parse_source(DELIVERY_SERVICE_SOURCE)
    module_constants = {
        target.id
        for node in delivery_tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }
    assert not module_constants & {
        "DELIVERY_ATTEMPT_TOTAL",
        "DELIVERY_FAILED_TOTAL",
        "ARTIFACT_GONE_TOTAL",
    }, "投递指标名不得再散落在调用方模块（归口到 metrics.CATALOG）"


# --- 2. 认证/探针/配置失败打点接线（真实源码依赖图） --------------------------------------


def test_b299_console_auth_routes_record_operation_outcomes() -> None:
    """[B-299] 三条公开 CHAT 认证路由都按 operation 记结局（认证失败与设置失败可区分）。"""
    tree = _parse_source(CONSOLE_AUTH_SOURCE)
    assert "muad_console_platform.metrics" in _imports(CONSOLE_AUTH_SOURCE)
    assert _count_outcome_operations(tree, "exchange_chat_token", "CHAT_AUTH_METRIC") == {"exchange"}
    assert _count_outcome_operations(tree, "read_chat_identity", "CHAT_AUTH_METRIC") == {"me"}
    assert _count_outcome_operations(tree, "logout_chat_session", "CHAT_AUTH_METRIC") == {"logout"}


def test_b299_console_probe_pool_and_config_paths_use_their_own_families() -> None:
    """[B-299] 探针结果/池等待/配置失败各自接到自己的目录家族（不混报）。"""
    probe_module = _parse_source(CONSOLE_PROBE_SOURCE)
    assert "muad_console_platform.metrics" in _imports(CONSOLE_PROBE_SOURCE)
    assert _calls(_function(probe_module, "probe_model_endpoint"), "_record_probe_outcome")
    assert _calls(_function(probe_module, "_perform_probe"), "record_pool_wait")
    run_one = _class_method(probe_module, "ModelProbeService", "_run_one")
    assert _calls(run_one, "_record_probe_outcome")
    recorder = _function(probe_module, "_record_probe_outcome")
    assert {
        _first_arg_name(call) for call in _calls(recorder, "record_outcome")
    } == {"MODEL_PROBE_METRIC"}
    assert {
        _first_arg_name(call) for call in _calls(recorder, "record_reason")
    } == {"MODEL_PROBE_FAILURE_METRIC"}
    probe_pools = _keyword_literals(_calls(run_one, "probe_model_endpoint"), "pool")
    assert probe_pools == {"model"}, "装配根探针必须显式声明自己的池"
    batch_pools = _keyword_literals(
        _calls(_parse_source(CONSOLE_MODEL_TEST_SOURCE), "probe_model_endpoint"), "pool"
    )
    assert batch_pools == {"batch"}, "批量测试路径不得冒充 model 池"

    request_method = _class_method(probe_module, "ProbeCapacityClient", "_request")
    assert {
        _string_args(call)[0] for call in _calls(request_method, "record_pool_wait") if _string_args(call)
    } == {"control"}

    http_tree = _parse_source(CONSOLE_HTTP_SOURCE)
    assert {
        _string_args(call)[0] for call in _calls(http_tree, "record_reason") if _string_args(call)
    } == {"budget_conflict"}
    deps_tree = _parse_source(CONSOLE_DEPS_SOURCE)
    assert {
        _string_args(call)[0] for call in _calls(deps_tree, "record_reason") if _string_args(call)
    } == {"missing_service_identity"}
    main_tree = _parse_source(CONSOLE_MAIN_SOURCE)
    assert {
        _string_args(call)[0] for call in _calls(main_tree, "record_reason") if _string_args(call)
    } == {"missing_service_identity"}


def test_b299_probe_reason_vocabulary_is_bounded() -> None:
    """[B-299] 探针 reason 是封闭字面量集合：label 值不会随错误码/资源 ID 漂移。"""
    from muad_console_platform.application.model_probe_service import ProbeReason

    reasons = set(get_args(ProbeReason))
    assert {
        "AVAILABLE",
        "CREDENTIAL_MISSING",
        "MODEL_UNAVAILABLE",
        "PROBE_HTTP_ERROR",
        "CONNECT_ERROR",
        "MODEL_TIMEOUT",
        "POOL_WAIT_TIMEOUT",
        "TRANSPORT_ERROR",
        "CAPACITY_REFUSED",
        "COMMON_NOT_FOUND",
        "MODEL_DISABLED",
    } <= reasons
    assert len(reasons) <= 16, f"reason 词表必须封闭（当前 {sorted(reasons)}）"


# --- 2. 探针/池等待真实打点（真实 HTTP，无共享数据） --------------------------------------


async def test_b299_probe_http_outcomes_are_recorded_with_bounded_reasons() -> None:
    """[B-299] 真实 HTTP 探测：可用/凭据失败/服务端失败/拒连各自落带边界 reason 的样本。"""
    async with _probe_server() as server:
        client = httpx.AsyncClient(timeout=httpx.Timeout(5.0), trust_env=False)
        try:
            before = render_metrics()
            ok = await probe_model_endpoint(
                client, model_id="gpt-b299", credential=_credential(server.url("/ok")), pool="model"
            )
            unauthorized = await probe_model_endpoint(
                client,
                model_id="gpt-b299",
                credential=_credential(server.url("/unauthorized")),
                pool="model",
            )
            failed = await probe_model_endpoint(
                client,
                model_id="gpt-b299",
                credential=_credential(server.url("/server-error")),
                pool="model",
            )
            refused = await probe_model_endpoint(
                client,
                model_id="gpt-b299",
                credential=_credential(_closed_port_url()),
                pool="model",
            )
            after = render_metrics()
        finally:
            await client.aclose()

    assert ok.status == "AVAILABLE" and ok.reason == "AVAILABLE"
    assert unauthorized.status == "FAILED" and unauthorized.reason == "CREDENTIAL_MISSING"
    assert unauthorized.error_code == "CREDENTIAL_MISSING"
    assert failed.reason == "MODEL_UNAVAILABLE"
    assert refused.reason == "CONNECT_ERROR" and refused.outcome == "NOT_SENT"

    assert _delta(after, before, "model_probe_total", {"status": "AVAILABLE"}) >= 1
    assert _delta(after, before, "model_probe_failure_total", {"reason": "CREDENTIAL_MISSING"}) >= 1
    assert _delta(after, before, "model_probe_failure_total", {"reason": "MODEL_UNAVAILABLE"}) >= 1
    assert _delta(after, before, "model_probe_failure_total", {"reason": "CONNECT_ERROR"}) >= 1
    assert CANARY_API_KEY not in after, "凭据原文不得进入指标文本"


async def test_b299_pool_wait_is_recorded_and_distinguished_from_config_failure() -> None:
    """[B-299] 真实池等待（连接被挂起请求占满）→ 池等待家族计数，且不触碰配置失败家族。"""
    async with _probe_server() as server:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=0.3),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
            trust_env=False,
        )
        try:
            hanging = asyncio.create_task(
                probe_model_endpoint(
                    client,
                    model_id="gpt-b299-hang",
                    credential=_credential(server.url("/hang")),
                    pool="model",
                )
            )
            await asyncio.sleep(0.15)
            before = render_metrics()
            waited = await probe_model_endpoint(
                client, model_id="gpt-b299", credential=_credential(server.url("/ok")), pool="model"
            )
            after = render_metrics()
            hanging.cancel()
            with suppress(asyncio.CancelledError):
                await hanging
        finally:
            await client.aclose()

    assert waited.status == "FAILED", "池等待超时按 B-228 口径仍是 FAILED/MODEL_UNAVAILABLE"
    assert waited.reason == "POOL_WAIT_TIMEOUT"
    assert waited.error_code == "MODEL_UNAVAILABLE" and waited.outcome == "UNKNOWN"
    assert _delta(after, before, "console_http_pool_wait_total", {"pool": "model"}) >= 1
    assert _delta(after, before, "model_probe_failure_total", {"reason": "POOL_WAIT_TIMEOUT"}) >= 1
    assert _delta(after, before, "console_config_failure_total", {"component": "probe_capacity"}) == 0


async def test_b299_capacity_port_pool_wait_is_recorded_on_the_control_pool() -> None:
    """[B-299] 容量端口（control 池）真实池等待：计数在 control 池，错误语义仍是内部错误。"""
    async with _probe_server(drip_all=True) as server:
        capacity = ProbeCapacityClient(
            server.url(),
            service_token=CANARY_SERVICE_TOKEN,
            timeout_sec=1.0,
            transport=httpx.AsyncHTTPTransport(
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
                trust_env=False,
            ),
        )
        try:
            held = asyncio.create_task(
                capacity.reserve(tenant_id="b299-tenant", body=_capacity_request(), trace_id="b299")
            )
            await asyncio.sleep(0.2)
            before = render_metrics()
            with pytest.raises(AppError) as raised:
                await capacity.reserve(tenant_id="b299-tenant", body=_capacity_request(), trace_id="b299")
            after = render_metrics()
            held.cancel()
            with suppress(asyncio.CancelledError):
                await held
        finally:
            await capacity.aclose()

    assert raised.value.code == ErrorCode.COMMON_INTERNAL_ERROR, "池等待不改变既有错误映射"
    assert _delta(after, before, "console_http_pool_wait_total", {"pool": "control"}) >= 1
    assert CANARY_SERVICE_TOKEN not in after, "服务身份不得进入指标文本"


# --- 1. 维护循环失败真实打点（真实拒连，不杀循环） ----------------------------------------


async def test_b299_maintenance_tick_failure_is_counted_and_keeps_the_loop_alive(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """[B-299] 维护循环连不上 PG：失败被计数+日志，循环继续（单次失败不静默、不杀循环）。"""
    engine = create_async_engine("postgresql+asyncpg://muad:muad@127.0.0.1:1/muad")
    loop = WorkerLoop(
        async_sessionmaker(engine, expire_on_commit=False),
        settings=_worker_settings(),
    )
    before = render_metrics()
    task = asyncio.create_task(loop.run_maintenance_forever())
    try:
        with caplog.at_level(logging.ERROR, logger="muad_agent_worker.worker.service"):
            deadline = asyncio.get_running_loop().time() + 10.0
            while _sample_value(render_metrics(), "worker_maintenance_total", {"status": "failed"}) == 0:
                assert asyncio.get_running_loop().time() < deadline, "维护失败未在预算内被计数"
                await asyncio.sleep(0.05)
            assert not task.done(), "单次回收失败不得杀死维护循环"
        after = render_metrics()
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await engine.dispose()

    assert _delta(after, before, "worker_maintenance_total", {"status": "failed"}) >= 1
    assert _delta(after, before, "worker_maintenance_total", {"status": "ok"}) == 0
    assert "worker_maintenance_reclaim_failed" in caplog.text


def _worker_settings() -> Any:
    from muad_common import SharedSettings

    return SharedSettings(run_reaper_interval_sec=1)


# --- 2/3. 配置失败 + 标准日志 tenant/trace 关联、不含 Token/Key ----------------------------


async def test_b299_console_config_failure_is_counted_and_logged_without_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-299] 缺探针服务身份：配置失败家族计数；日志带 tenant/trace，且不回显 Token。"""
    monkeypatch.setenv("AGENT_RUNTIME_URL", "")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", CANARY_SERVICE_TOKEN)
    service = f"muad-console-b299-{uuid.uuid4().hex[:8]}"
    trace_id = f"b299-trace-{uuid.uuid4().hex[:8]}"
    tenant_id = f"b299-tenant-{uuid.uuid4().hex[:8]}"
    request = Request({"type": "http", "app": FastAPI(), "method": "POST", "path": "/", "headers": []})

    root = logging.getLogger()
    handlers = list(root.handlers)
    configure_logging(service, log_dir=tmp_path, console=False)
    try:
        before = render_metrics()
        with context_scope(tenant_id=tenant_id, trace_id=trace_id):
            with pytest.raises(AppError) as raised:
                get_probe_capacity_client(request)
        after = render_metrics()
    finally:
        logging.shutdown()
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in handlers:
            root.addHandler(handler)

    assert raised.value.code == ErrorCode.COMMON_INTERNAL_ERROR
    assert (
        _delta(
            after,
            before,
            "console_config_failure_total",
            {"component": "probe_capacity", "reason": "missing_service_identity"},
        )
        >= 1
    )
    records = [
        json.loads(line)
        for line in _log_file(tmp_path, service).read_text(encoding="utf-8").splitlines()
        if line.startswith("{")
    ]
    failure_records = [
        record for record in records if "probe_capacity_client_unconfigured" in str(record["message"])
    ]
    assert failure_records, f"缺配置必须留下指名日志：{records}"
    record = failure_records[-1]
    assert record["trace_id"] == trace_id and record["tenant_id"] == tenant_id
    assert "AGENT_RUNTIME_URL" in str(record["message"]), "日志必须指名缺的配置项"
    assert CANARY_SERVICE_TOKEN not in json.dumps(record, ensure_ascii=False), "Token 不得进日志"


def _log_file(tmp_path: Path, service: str) -> Path:
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    return tmp_path / service / f"{day}.log"


def test_b299_probe_budget_conflict_is_counted_as_config_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """[B-299] 部署池读预算与探针契约预算冲突：配置失败计数 + 显式 ValueError（不静默取小值）。"""
    from muad_common import SharedSettings
    from muad_console_platform.infrastructure.http_clients import probe_budget

    monkeypatch.setenv("MODEL_HTTP_READ_TIMEOUT_SEC", "3")
    before = render_metrics()
    with pytest.raises(ValueError, match="probe single-shot"):
        probe_budget(SharedSettings())
    after = render_metrics()
    assert (
        _delta(
            after,
            before,
            "console_config_failure_total",
            {"component": "probe_budget", "reason": "budget_conflict"},
        )
        >= 1
    )


# --- 2. 认证结局可区分（认证错误 vs 设置失败） --------------------------------------------


def test_b299_chat_auth_metric_distinguishes_auth_errors_from_settings_failure() -> None:
    """[B-299] 认证结局按目录错误码分桶：Token 类失败与平台设置失败是不同样本。"""
    before = render_metrics()
    with pytest.raises(AppError):
        with console_metrics.count_outcome(console_metrics.CHAT_AUTH_METRIC, {"operation": "exchange"}):
            raise AppError(ErrorCode.CHAT_TOKEN_INVALID)
    with pytest.raises(AppError):
        with console_metrics.count_outcome(console_metrics.CHAT_AUTH_METRIC, {"operation": "exchange"}):
            raise AppError(ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE)
    with console_metrics.count_outcome(console_metrics.CHAT_AUTH_METRIC, {"operation": "me"}):
        pass
    after = render_metrics()

    assert (
        _delta(
            after, before, "chat_auth_total", {"operation": "exchange", "status": "CHAT_TOKEN_INVALID"}
        )
        >= 1
    )
    assert (
        _delta(
            after,
            before,
            "chat_auth_total",
            {"operation": "exchange", "status": "PLATFORM_SETTINGS_UNAVAILABLE"},
        )
        >= 1
    )
    assert _delta(after, before, "chat_auth_total", {"operation": "me", "status": "SUCCESS"}) >= 1
