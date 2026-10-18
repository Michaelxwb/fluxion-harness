"""B-294 / B-09：Runtime 装配所有通道端点与独立资源池（design §3.2/§3.7/§4.1；TASK-094）。

真实边界（不 mock 装配、路由、分池与生命周期）：

- **真实 Runtime 装配根**：`muad_agent_runtime.main.lifespan`（真实 `validate_startup`、真实
  分池、真实 uvicorn socket、真实 `/readyz` 探针、真实后台循环任务）；
- **真实 Console 设置源（Owner）**：`ConsolePlatformSettingsClient` 打真实本地 uvicorn
  （真实 socket + 真实封套），按 API-19 `GET /internal/v1/platform-settings` 应答并记录每次到达；
- **隔离 PostgreSQL/Redis**：空库 + `alembic upgrade head`（跑完 drop），保留清理候选、准入
  策略行都是真实行；Redis 用隔离号位（本文件还会用不可达地址证明它是可选 hint）；
- **真实共享文件系统**：隔离 `ARTIFACT_ROOT` 下的暂存字节由真实清理删除。

关键断言（B-294 契约）：

1. **生产 app 注册历史/控制/通知/交付等实际端点**：路由表与方法集逐条钉住（不是探针 app），
   依赖图证明运行面/上传面经注入的单一设置源收口，真实 HTTP 证明端点真的可达；
2. **注入分池 HTTP 与监督预算**：control/model/mcp/egress 四池由装配根建、预算唯一来源是启动
   设置、调用方消费**同一实例**、退出即关；本进程不建的池名（stream/upload）显式失败；
3. **独立维护循环**：保留清理/投递泵/期限扫描/回收/槽监督/控制/续跑/记忆各自成任务，退出即停
   （退出后保留清理不再触达设置源、无悬挂任务、分池全关）；
4. **readiness 与 Redis 可选退化一致**：Redis 不可达仍就绪（降级为无 hint），真实依赖
   （artifact storage）失败即 503 并指名失败项。

B-09 契约（同一文件的配置面）：contracts 配置解析与真实 PG 快照/准入版本、启动 settings 唯一
来源、派生值唯一；坏设置显式失败且不回落默认；配置网络读取在行锁短事务外（结构断言 + 真实
行为：低版本不覆盖全局容量）。本文件的池预算/保留期都是**用例参数**，不代表生产实测容量。
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import inspect
import os
import socket
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from muad_agent_runtime.application.channel_notifications import (
    DELIVERY_NOT_REQUIRED,
    KIND_RUN_COMPLETED,
    write_run_notification,
)
from muad_agent_runtime.application.channel_retention import (
    RetentionPolicy,
    retention_policy_from_settings,
)
from muad_agent_runtime.application.channel_uploads import (
    ChannelUploadLimits,
    register_staged_upload,
)
from muad_agent_runtime.application.run_admission import AdmissionPolicyService
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_runtime.infrastructure.cancel_hint import NullCancelHintStore
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.http_clients import (
    RUNTIME_HTTP_POOLS,
    RuntimeHttpPools,
    pool_limits,
    pool_timeout,
)
from muad_agent_runtime.infrastructure.models.runtime import (
    ChannelNotification,
    ChannelUpload,
    Conversation,
    RunAdmissionPolicy,
    RunRecord,
)
from muad_agent_runtime.main import app
from muad_api import AppError
from muad_artifact_store import NfsArtifactStore
from muad_common import SharedSettings
from muad_contracts.enums import RunStatus
from muad_contracts.platform_settings import PlatformSettingsError

from agent_runtime.conftest import TenantContext

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_SETTINGS_PATH = "/internal/v1/platform-settings"
INTERNAL_TOKEN = "test-internal-token"

#: 启动预算（唯一来源是 SharedSettings；装配根不复制默认值）。与默认不同，钉住真的注入了设置值。
CONTROL_LIMITS = (6, 3)
MODEL_LIMITS = (9, 4)
MCP_LIMITS = (5, 2)
EGRESS_LIMITS = (7, 3)
CONTROL_READ_SEC = 7.0
MODEL_READ_SEC = 11.0
MCP_READ_SEC = 13.0
EGRESS_READ_SEC = 17.0
CONNECT_TIMEOUT_SEC = 4.0
POOL_TIMEOUT_SEC = 3.0
RUNTIME_SLOTS = 3
#: 维护循环节拍（沿用既有回收节拍，不新增同义键）。
REAPER_INTERVAL_SEC = 1

#: 保留期/宽限（用例参数，非生产实测）：过期通知与过期暂存都必须被真实清理。
NOTIFICATION_RETENTION_DAYS = 1
STREAM_REPLAY_RETENTION_HOURS = 1
HISTORY_RETENTION_DAYS = 0
NOTIFICATION_AGE = timedelta(days=40)
STAGING_AGE = timedelta(hours=3)
STAGING_FILE_AGE = timedelta(hours=3)
STAGING_TTL_SEC = 3600
STAGED_BYTES = b"runtime wiring staged bytes\n"

#: 装配根启动的**独立**维护/执行循环（各自成任务；B-294 的可观测面）。
LOOP_NAMES = (
    "runtime.continuation",
    "runtime.memory",
    "runtime.deadline",
    "runtime.control",
    "runtime.reaper",
    "runtime.execution_slots",
    "runtime.delivery",
    "runtime.retention",
)
RETENTION_LOOP_NAME = "runtime.retention"

WAIT_TIMEOUT_SEC = 25.0
HTTP_TIMEOUT_SEC = 10.0

#: 生产 app 的通道面路由表（方法集逐条钉住；路径来自真实 include_router）。
CHANNEL_SURFACE: dict[str, set[str]] = {
    "/v1/runs": {"POST"},
    "/v1/runs/{run_id}": {"GET"},
    "/v1/runs/{run_id}/events": {"GET"},
    "/v1/runs/{run_id}/cancel": {"POST"},
    "/v1/runs/{run_id}/resume": {"POST"},
    "/v1/runs/cancel-active": {"POST"},
    "/v1/conversations": {"POST"},
    "/internal/webchat/entries/{entry_id}/conversations": {"GET"},
    "/internal/webchat/conversations/{conversation_id}": {"GET"},
    "/internal/webchat/conversations/{conversation_id}/messages": {"GET"},
    "/internal/webchat/conversations/{conversation_id}/scope": {"GET"},
    "/internal/webchat/uploads": {"POST"},
    "/internal/webchat/notifications": {"GET"},
    "/internal/webchat/notifications/{notification_id}/read": {"POST"},
    "/internal/webchat/artifacts/{artifact_id}": {"GET"},
    "/internal/webchat/task-results": {"POST"},
    "/internal/channel-notifications/reserve": {"POST"},
    "/internal/channel-notifications/{notification_id}/renew": {"POST"},
    "/internal/channel-notifications/{notification_id}/ack": {"POST"},
    "/internal/admin/runs": {"GET"},
    "/internal/admin/runs/{run_id}": {"GET"},
    "/internal/artifacts/{artifact_id}": {"GET"},
    "/internal/tool-results": {"POST"},
    "/internal/model-capacity/reservations": {"POST"},
    "/internal/model-capacity/reservations/{reservation_id}/renew": {"POST"},
    "/internal/model-capacity/reservations/{reservation_id}/settle": {"POST"},
    "/internal/admin/users/{user_id}/memory": {"GET", "DELETE"},
    "/internal/admin/users/{user_id}/memory/commands": {"POST"},
    "/internal/admin/users/{user_id}/memory/{memory_key}/history": {"GET"},
}


# --------------------------------------------------------------------------------------
# 真实 HTTP 封套 / 真实 Console 设置源（真实 socket）
# --------------------------------------------------------------------------------------


def _envelope(data: Any = None) -> dict[str, Any]:
    return {
        "code": "0",
        "msg": "ok",
        "data": data,
        "trace_id": "trace-b294",
        "request_id": "request-b294",
        "timestamp": "2026-10-18T07:00:00Z",
    }


def _settings_document(
    *,
    notification_days: int = NOTIFICATION_RETENTION_DAYS,
    stream_hours: int = STREAM_REPLAY_RETENTION_HOURS,
    history_days: int = HISTORY_RETENTION_DAYS,
    tenant_limit: int = 500,
    user_limit: int = 4,
) -> dict[str, Any]:
    """真实平台设置文档（只含本文件消费的分组；解析走 contracts 的同一 schema）。"""
    return {
        "capacity": {
            "max_open_runs_per_tenant": tenant_limit,
            "max_open_runs_per_user": user_limit,
        },
        "webchat": {
            "stream_replay_retention_hours": stream_hours,
            "notification_retention_days": notification_days,
            "history_retention_days": history_days,
        },
    }


@dataclass
class _ConsoleSource:
    """真实 Console 设置源桩：uvicorn + 真实 socket，按文档应答并记录每次到达。"""

    app: FastAPI
    revision: int = 7
    document: dict[str, Any] = field(default_factory=_settings_document)
    calls: list[dict[str, str]] = field(default_factory=list)

    async def handle(self, request: Request) -> JSONResponse:
        self.calls.append(dict(request.headers))
        return JSONResponse(
            _envelope({"revision": self.revision, "settings": self.document})
        )


@contextlib.asynccontextmanager
async def _serve(target: FastAPI) -> AsyncIterator[str]:
    """真实 uvicorn 监听 127.0.0.1 随机端口，产出真实 base_url（与生产同一 HTTP 栈）。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    config = uvicorn.Config(target, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        deadline = time.monotonic() + HTTP_TIMEOUT_SEC
        while not server.started and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert server.started, "真实服务未在超时内监听"
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(serving, timeout=HTTP_TIMEOUT_SEC)


# --------------------------------------------------------------------------------------
# 隔离数据存储 + Runtime 进程环境
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _WiringEnv:
    tenant: TenantContext
    root: Path
    console: _ConsoleSource


@pytest.fixture
async def wiring_env(
    async_tool_datastore: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[_WiringEnv]:
    """隔离 PostgreSQL/Redis 号位 + Runtime 进程环境 + 真实 Console 设置源；跑完 drop。"""
    root = tmp_path / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    tenant = TenantContext(
        tenant_id=f"test-{uuid.uuid4()}",
        agent_id=uuid.uuid4(),
        platform_user_id=uuid.uuid4(),
    )
    console = _ConsoleSource(app=FastAPI())
    console.app.get(PLATFORM_SETTINGS_PATH)(console.handle)
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    monkeypatch.setenv("ARTIFACT_ROOT", str(root))
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", INTERNAL_TOKEN)
    monkeypatch.setenv("DEFAULT_TENANT_ID", tenant.tenant_id)
    monkeypatch.setenv("RUNTIME_EXECUTION_CONCURRENCY", str(RUNTIME_SLOTS))
    monkeypatch.setenv("RUN_REAPER_INTERVAL_SEC", str(REAPER_INTERVAL_SEC))
    monkeypatch.setenv("CONTROL_HTTP_MAX_CONNECTIONS", str(CONTROL_LIMITS[0]))
    monkeypatch.setenv("CONTROL_HTTP_MAX_KEEPALIVE", str(CONTROL_LIMITS[1]))
    monkeypatch.setenv("MODEL_HTTP_MAX_CONNECTIONS", str(MODEL_LIMITS[0]))
    monkeypatch.setenv("MODEL_HTTP_MAX_KEEPALIVE", str(MODEL_LIMITS[1]))
    monkeypatch.setenv("MCP_HTTP_MAX_CONNECTIONS", str(MCP_LIMITS[0]))
    monkeypatch.setenv("MCP_HTTP_MAX_KEEPALIVE", str(MCP_LIMITS[1]))
    monkeypatch.setenv("EGRESS_HTTP_MAX_CONNECTIONS", str(EGRESS_LIMITS[0]))
    monkeypatch.setenv("EGRESS_HTTP_MAX_KEEPALIVE", str(EGRESS_LIMITS[1]))
    monkeypatch.setenv("CONTROL_HTTP_READ_TIMEOUT_SEC", str(CONTROL_READ_SEC))
    monkeypatch.setenv("MODEL_HTTP_READ_TIMEOUT_SEC", str(MODEL_READ_SEC))
    monkeypatch.setenv("MCP_HTTP_READ_TIMEOUT_SEC", str(MCP_READ_SEC))
    monkeypatch.setenv("EGRESS_HTTP_READ_TIMEOUT_SEC", str(EGRESS_READ_SEC))
    monkeypatch.setenv("HTTP_CONNECT_TIMEOUT_SEC", str(CONNECT_TIMEOUT_SEC))
    monkeypatch.setenv("HTTP_POOL_TIMEOUT_SEC", str(POOL_TIMEOUT_SEC))
    await runtime_db.dispose_engine()
    try:
        async with _serve(console.app) as url:
            monkeypatch.setenv("CONSOLE_PLATFORM_URL", url)
            yield _WiringEnv(tenant=tenant, root=root, console=console)
    finally:
        await runtime_db.dispose_engine()


# --------------------------------------------------------------------------------------
# 观测助手：路由表 / 分池有效值 / 循环任务 / 等待
# --------------------------------------------------------------------------------------


def _effective_routes() -> list[Any]:
    """生产 app 的有效路由：FastAPI 的 `include_router` 惰性展开（`_IncludedRouter`）。"""
    routes: list[Any] = []
    for route in app.routes:
        expand = getattr(route, "effective_route_contexts", None)
        if expand is not None:
            routes.extend(expand())
        else:
            routes.append(route)
    return routes


def _route_methods(path: str) -> set[str]:
    methods: set[str] = set()
    for route in _effective_routes():
        if getattr(route, "path", None) == path:
            methods |= {str(method) for method in getattr(route, "methods", set())}
    if not methods:
        raise AssertionError(f"route not registered: {path}")
    return methods


def _dependency_names(dependant: Any) -> set[str]:
    names: set[str] = set()

    def walk(node: Any) -> None:
        call = getattr(node, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", str(call)))
        for child in getattr(node, "dependencies", ()):  # pragma: no branch - 图是有限的
            walk(child)

    walk(dependant)
    return names


def _route_dependencies(path: str, method: str) -> set[str]:
    for route in _effective_routes():
        if getattr(route, "path", None) != path:
            continue
        if method not in {str(item) for item in getattr(route, "methods", set())}:
            continue
        return _dependency_names(route.dependant)
    raise AssertionError(f"route not registered: {method} {path}")


def _effective_pool_limits(client: httpx.AsyncClient) -> tuple[int, int]:
    """httpx 不暴露池预算：从真实 transport 的 httpcore 池读**有效值**（装配断言的直接证据）。"""
    pool = client._transport._pool  # type: ignore[attr-defined]
    return pool._max_connections, pool._max_keepalive_connections  # type: ignore[attr-defined]


def _loop_tasks(names: tuple[str, ...]) -> dict[str, asyncio.Task[Any]]:
    found: dict[str, asyncio.Task[Any]] = {}
    for task in asyncio.all_tasks():
        if task.get_name() in names:
            found[task.get_name()] = task
    return found


async def _wait_for(
    predicate: Callable[[], Any],
    *,
    message: str,
    timeout: float = WAIT_TIMEOUT_SEC,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"condition not met within {timeout}s: {message}")


@contextlib.asynccontextmanager
async def _running_runtime() -> AsyncIterator[str]:
    """真实 Runtime：真实 uvicorn socket + 真实 lifespan（装配根的全部循环都在跑）。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        await _wait_for(lambda: server.started, message="runtime 未在超时内监听")
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(serving, timeout=HTTP_TIMEOUT_SEC)


def _internal_headers(tenant_id: str) -> dict[str, str]:
    return {"X-Tenant-Id": tenant_id, "X-Internal-Service": INTERNAL_TOKEN}


def _function_node(source: str, name: str) -> ast.AST:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"function not found in source: {name}")


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute):
            names.add(func.attr)
        elif isinstance(func, ast.Name):
            names.add(func.id)
    return names


# --------------------------------------------------------------------------------------
# 真实行播种（隔离库；清理候选只有真实行与真实文件）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Seeded:
    notification_id: uuid.UUID
    upload_id: uuid.UUID
    storage_path: Path


async def _seed_retention_candidates(
    tenant: TenantContext, root: Path, *, delivery_state: str = DELIVERY_NOT_REQUIRED
) -> _Seeded:
    """真实会话 + Run + 通知行 + 暂存登记与暂存字节；老化只拨时间列/文件 mtime，不改事实。"""
    entry_id = uuid.uuid4()
    factory = get_session_factory()
    async with factory() as session:
        conversation = Conversation(
            tenant_id=tenant.tenant_id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            title="装配用例",
            status="ACTIVE",
            last_seq=0,
            channel_account_id=entry_id,
            last_message_at=datetime.now(UTC),
        )
        session.add(conversation)
        await session.flush()
        run = RunRecord(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            snapshot_id=uuid.uuid4(),
            status=RunStatus.COMPLETED,
            input_text="装配用例输入",
            trace_id=uuid.uuid4().hex,
        )
        session.add(run)
        await session.flush()
        receipt = await write_run_notification(
            session,
            run=run,
            kind=KIND_RUN_COMPLETED,
            delivery_key=f"run:{run.id}:terminal",
            delivery_state=delivery_state,
        )
        assert receipt is not None, "网页入口会话必须有入箱行"
        await session.execute(
            sa.update(ChannelNotification)
            .where(ChannelNotification.id == receipt.id)
            .values(create_time=datetime.now(UTC) - NOTIFICATION_AGE)
        )
        storage_key = f"staging/{uuid.uuid4().hex}.txt"
        upload = await register_staged_upload(
            session,
            tenant_id=tenant.tenant_id,
            user_id=tenant.platform_user_id,
            entry_id=entry_id,
            storage_key=storage_key,
            kind="DOCUMENT",
            media_type="text/plain",
            checksum="sha256:" + hashlib.sha256(STAGED_BYTES).hexdigest(),
            filename="staged.txt",
            size=len(STAGED_BYTES),
            limits=ChannelUploadLimits(
                upload_max_bytes=1024 * 1024,
                upload_max_per_message=5,
                staging_ttl_sec=STAGING_TTL_SEC,
            ),
        )
        moment = datetime.now(UTC)
        await session.execute(
            sa.update(ChannelUpload)
            .where(ChannelUpload.id == upload.id)
            .values(create_time=moment - STAGING_AGE - timedelta(hours=1), expires_at=moment - STAGING_AGE)
        )
        await session.commit()
        path = NfsArtifactStore(root).resolve(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(STAGED_BYTES)
        stamp = (datetime.now(UTC) - STAGING_FILE_AGE).timestamp()
        os.utime(path, (stamp, stamp))
        return _Seeded(notification_id=receipt.id, upload_id=upload.id, storage_path=path)


async def _notification_hidden(notification_id: uuid.UUID) -> bool:
    async with get_session_factory()() as session:
        return bool(
            await session.scalar(
                sa.select(ChannelNotification.is_deleted).where(
                    ChannelNotification.id == notification_id
                )
            )
        )


async def _upload_hidden(upload_id: uuid.UUID) -> bool:
    async with get_session_factory()() as session:
        return bool(
            await session.scalar(
                sa.select(ChannelUpload.is_deleted).where(ChannelUpload.id == upload_id)
            )
        )


async def _policy_row(tenant_id: str) -> RunAdmissionPolicy:
    async with get_session_factory()() as session:
        row = await session.scalar(
            sa.select(RunAdmissionPolicy).where(RunAdmissionPolicy.tenant_id == tenant_id)
        )
        assert row is not None
        return row


# --------------------------------------------------------------------------------------
# 1. 生产 app 注册通道端点（路由表 + 依赖图 + 真实 HTTP）
# --------------------------------------------------------------------------------------


async def test_b294_production_app_registers_the_full_channel_surface(
    wiring_env: _WiringEnv,
) -> None:
    """[B-294] 历史/控制/通知/交付端点都是生产 app 的真实路由，真实 HTTP 可达。"""
    for path, methods in CHANNEL_SURFACE.items():
        assert _route_methods(path) == methods, f"通道端点未注册或方法集不符: {path}"

    # 运行面与上传/通知面都经**注入的单一设置源**与装配根工厂收口（不是各自再造一份）。
    run_deps = _route_dependencies("/v1/runs", "POST")
    assert {"get_platform_settings_client", "get_executor_factory", "get_resolve_client"} <= run_deps
    upload_deps = _route_dependencies("/internal/webchat/uploads", "POST")
    assert {"get_platform_settings_client", "require_internal_service"} <= upload_deps
    notifications_deps = _route_dependencies("/internal/webchat/notifications", "GET")
    assert {"get_platform_settings_client", "require_internal_service"} <= notifications_deps

    async with _running_runtime() as base_url:
        async with httpx.AsyncClient(base_url=base_url, timeout=HTTP_TIMEOUT_SEC) as client:
            ready = await client.get("/readyz")
            assert ready.status_code == 200, ready.text
            assert ready.json()["data"]["status"] == "ready"

            # 真实端点：服务身份门控 + 真实封套（缺门控 403、缺表单 422 —— 都不是 404）。
            denied = await client.post(
                "/internal/webchat/uploads", headers={"X-Tenant-Id": wiring_env.tenant.tenant_id}
            )
            assert denied.status_code == 403
            invalid = await client.post(
                "/internal/webchat/uploads",
                headers=_internal_headers(wiring_env.tenant.tenant_id),
            )
            assert invalid.status_code == 422, invalid.text

            # 真实读取面：空 feed 的增量读取（真实 PG，不是替身）。
            feed = await client.get(
                "/internal/webchat/notifications",
                params={"user_id": str(wiring_env.tenant.platform_user_id), "after_seq": 0},
                headers=_internal_headers(wiring_env.tenant.tenant_id),
            )
            assert feed.status_code == 200, feed.text
            assert feed.json()["data"]["items"] == []
            assert feed.json()["data"]["next_seq"] == 0


# --------------------------------------------------------------------------------------
# 2. 装配根注入分池预算与监督预算（预算唯一来源 = 启动设置）
# --------------------------------------------------------------------------------------


async def test_b294_assembly_root_injects_per_pool_budgets_and_closes_them_on_exit(
    wiring_env: _WiringEnv,
) -> None:
    """[B-294] 四池预算/期限来自启动设置、调用方消费同一实例、退出即关。"""
    from muad_agent_runtime import main

    settings = SharedSettings()
    baseline = set(asyncio.all_tasks())
    async with main.lifespan(main.app):
        pools = main.app.state.http_pools
        assert isinstance(pools, RuntimeHttpPools)
        assert len({id(pools.control), id(pools.model), id(pools.mcp), id(pools.egress)}) == 4, (
            "控制/模型/MCP/业务各自建池，不共享连接"
        )

        # 有效池大小 = 启动设置值（装配根不复制默认值；两处派生自同一 settings）
        for pool_name in RUNTIME_HTTP_POOLS:
            client = getattr(pools, pool_name)
            derived = pool_limits(settings, pool_name)  # type: ignore[arg-type]
            assert _effective_pool_limits(client) == (
                derived.max_connections,
                derived.max_keepalive_connections,
            )
        assert _effective_pool_limits(pools.control) == CONTROL_LIMITS
        assert _effective_pool_limits(pools.model) == MODEL_LIMITS
        assert _effective_pool_limits(pools.mcp) == MCP_LIMITS
        assert _effective_pool_limits(pools.egress) == EGRESS_LIMITS

        # 期限：连接/池超时全进程统一，读超时按调用类别（§3.7 `*_HTTP_READ_TIMEOUT_SEC`）
        for client, read in (
            (pools.control, CONTROL_READ_SEC),
            (pools.model, MODEL_READ_SEC),
            (pools.mcp, MCP_READ_SEC),
            (pools.egress, EGRESS_READ_SEC),
        ):
            assert (client.timeout.read, client.timeout.connect, client.timeout.pool) == (
                read,
                CONNECT_TIMEOUT_SEC,
                POOL_TIMEOUT_SEC,
            )

        # 内部服务调用不走环境代理（控制/业务池）；模型/MCP 是外部调用，保持 httpx 默认。
        assert pools.control._trust_env is False  # type: ignore[attr-defined]
        assert pools.egress._trust_env is False  # type: ignore[attr-defined]
        assert pools.model._trust_env is True  # type: ignore[attr-defined]
        assert pools.mcp._trust_env is True  # type: ignore[attr-defined]

        # 调用方消费装配根注入的**同一实例**（不是各自再造一个池）
        assert main.app.state.resolve_client._client is pools.control
        assert main.app.state.credentials_client._client is pools.control
        assert main.app.state.platform_settings_client._client is pools.control
        assert main.app.state.control_dispatcher._client is pools.control
        assert main.app.state.delivery_pump._sender._client._client is pools.egress
        factory = main.app.state.executor_factory
        assert factory.keywords["model_client"] is pools.model
        assert factory.keywords["mcp_client"] is pools.mcp
        assert factory.keywords["task_client"]._client is pools.egress
        assert factory.keywords["delivery_client"]._client is pools.egress
        assert main.app.state.consolidation_invoker._client is pools.model

        # 监督预算：执行槽来自每进程启动配置（§3.7 RUNTIME_EXECUTION_CONCURRENCY）
        assert main.app.state.execution_supervisor.slot_limit == RUNTIME_SLOTS
        assert not pools.control.is_closed and not pools.egress.is_closed
    assert all(getattr(pools, name).is_closed for name in RUNTIME_HTTP_POOLS), (
        "装配根建的池随进程关闭"
    )
    leftover = [
        task
        for task in asyncio.all_tasks() - baseline
        if not task.done() and task.get_name() in LOOP_NAMES
    ]
    assert leftover == [], f"存在悬挂的维护循环任务: {leftover}"


# --------------------------------------------------------------------------------------
# 3. 独立维护循环：保留清理按设置策略真实清理，且退出即停
# --------------------------------------------------------------------------------------


async def test_b294_retention_sweep_runs_as_its_own_loop_with_the_settings_policy(
    wiring_env: _WiringEnv,
) -> None:
    """[B-294] 保留清理是独立循环：真实清理过期通知/暂存，退出后不再触达设置源。"""
    from muad_agent_runtime import main

    wiring_env.console.revision = 11
    wiring_env.console.document = _settings_document(
        notification_days=NOTIFICATION_RETENTION_DAYS,
        stream_hours=STREAM_REPLAY_RETENTION_HOURS,
        history_days=HISTORY_RETENTION_DAYS,
    )
    seeded = await _seed_retention_candidates(wiring_env.tenant, wiring_env.root)

    async with main.lifespan(main.app):
        task = main.app.state.retention_task
        assert task.get_name() == RETENTION_LOOP_NAME
        assert not task.done(), "保留清理必须作为独立循环任务运行"
        await _wait_for(
            lambda: _notification_hidden(seeded.notification_id),
            message="过期通知未被保留清理隐藏",
        )
        await _wait_for(
            lambda: _upload_hidden(seeded.upload_id),
            message="过期暂存登记未被保留清理隐藏",
        )
        assert not seeded.storage_path.exists(), "过期暂存字节必须被真实删除"
        assert not task.done(), "单轮清理失败或完成后循环必须继续运行"

        # 清理边界来自平台设置快照（真实 HTTP，装配根注入的同一设置源）
        assert wiring_env.console.calls, "保留清理必须从平台设置源取一次边界"
        assert {call.get("x-tenant-id") for call in wiring_env.console.calls} == {
            wiring_env.tenant.tenant_id
        }
        calls_at_exit = len(wiring_env.console.calls)

    # 退出即停：保留清理循环不再运行（节拍 1s，等待 2 个节拍仍无新到达）
    assert task.done()
    await asyncio.sleep(2 * REAPER_INTERVAL_SEC)
    assert len(wiring_env.console.calls) == calls_at_exit, "退出后保留清理仍在触达设置源"


async def test_b09_bad_platform_settings_fail_explicitly_without_falling_back_to_defaults(
    wiring_env: _WiringEnv, caplog: pytest.LogCaptureFixture
) -> None:
    """[B-09] 坏设置不回落默认：清理循环显式失败、不清理、不静默取 schema 默认值。"""
    from muad_agent_runtime import main

    wiring_env.console.document = {"webchat": {"notification_retention_days": 0}}
    seeded = await _seed_retention_candidates(wiring_env.tenant, wiring_env.root)

    with caplog.at_level("ERROR"):
        async with main.lifespan(main.app):
            await _wait_for(
                lambda: any(
                    record.message == "webchat_retention_settings_failed" for record in caplog.records
                ),
                message="坏设置必须显式失败并留痕",
            )
            assert not await _notification_hidden(seeded.notification_id), (
                "坏设置不得回落默认值去清理数据"
            )
            assert not await _upload_hidden(seeded.upload_id)
            assert main.app.state.retention_task.done() is False, "单轮失败不得杀死循环"


# --------------------------------------------------------------------------------------
# 4. 独立循环集合与退出即停（装配根生命周期）
# --------------------------------------------------------------------------------------


async def test_b294_lifespan_runs_independent_maintenance_loops_and_stops_them_on_exit(
    wiring_env: _WiringEnv,
) -> None:
    """[B-294] 各循环独立成任务、退出即停；退出关闭全部泵/客户端/分池，无悬挂任务。"""
    from muad_agent_runtime import main

    baseline = set(asyncio.all_tasks())
    async with main.lifespan(main.app):
        loops = _loop_tasks(LOOP_NAMES)
        assert set(loops) == set(LOOP_NAMES), f"缺少独立循环任务: {set(loops) ^ set(LOOP_NAMES)}"
        assert all(not task.done() for task in loops.values())
        assert len({id(task) for task in loops.values()}) == len(LOOP_NAMES), "循环必须各自成任务"
        assert main.app.state.delivery_pump.inflight == 0
        assert main.app.state.execution_supervisor.execution_count == 0
        assert not main.app.state.http_pools.control.is_closed
        calls_during = len(wiring_env.console.calls)
        await asyncio.sleep(REAPER_INTERVAL_SEC + 0.5)
        assert len(wiring_env.console.calls) > calls_during, "保留清理循环在运行期必须真的推进"

    assert all(task.done() for task in loops.values()), "退出即停：循环任务必须全部结束"
    leftover = [
        task
        for task in asyncio.all_tasks() - baseline
        if not task.done() and task.get_name() in LOOP_NAMES
    ]
    assert leftover == [], f"存在悬挂的维护循环任务: {leftover}"
    assert all(getattr(main.app.state.http_pools, name).is_closed for name in RUNTIME_HTTP_POOLS)
    assert main.app.state.resolve_client._client.is_closed
    assert main.app.state.platform_settings_client._client.is_closed


# --------------------------------------------------------------------------------------
# 5. readiness 与 Redis 可选退化一致
# --------------------------------------------------------------------------------------


async def test_b294_readiness_keeps_redis_optional_and_fails_on_real_dependencies(
    wiring_env: _WiringEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[B-294] Redis 不可达仍就绪（可选 hint 降级）；真实依赖失败即 503 并指名失败项。"""
    from muad_agent_runtime import main

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    async with _running_runtime() as base_url:
        assert isinstance(main.app.state.cancel_hint_store, NullCancelHintStore), (
            "Redis 只是可选 hint：不可达时降级而不是把全部 Pod 退出就绪"
        )
        async with httpx.AsyncClient(base_url=base_url, timeout=HTTP_TIMEOUT_SEC) as client:
            ready = await client.get("/readyz")
            assert ready.status_code == 200, ready.text
            assert ready.json()["data"]["status"] == "ready"

            # 真实依赖（artifact storage）失败 ⇒ 不就绪，且失败项指名
            monkeypatch.setenv("ARTIFACT_ROOT", str(wiring_env.root / "missing"))
            unavailable = await client.get("/readyz")
            assert unavailable.status_code == 503, unavailable.text
            assert unavailable.json()["data"]["failed"] == ["artifact_storage"]


# --------------------------------------------------------------------------------------
# 6. B-09：启动预算/保留边界唯一来源、未调用池显式失败、准入版本单调
# --------------------------------------------------------------------------------------


async def test_b09_startup_and_platform_budgets_have_one_source_and_unused_pools_fail(
    wiring_env: _WiringEnv,
) -> None:
    """[B-09] 池预算与保留策略的默认/派生值唯一；本进程不建的池名显式失败。"""
    assert RUNTIME_HTTP_POOLS == ("control", "model", "mcp", "egress")
    assert {item.name for item in fields(RuntimeHttpPools)} == set(RUNTIME_HTTP_POOLS)

    settings = SharedSettings()
    for name, limits, read in (
        ("control", CONTROL_LIMITS, CONTROL_READ_SEC),
        ("model", MODEL_LIMITS, MODEL_READ_SEC),
        ("mcp", MCP_LIMITS, MCP_READ_SEC),
        ("egress", EGRESS_LIMITS, EGRESS_READ_SEC),
    ):
        derived = pool_limits(settings, name)  # type: ignore[arg-type]
        assert (derived.max_connections, derived.max_keepalive_connections) == limits
        assert pool_timeout(settings, name).read == read  # type: ignore[arg-type]

    # 未调用的池名显式失败：不静默按别的池预算建一个没人调用的池（design §3.7）
    for unused in ("stream", "upload", "nonsense"):
        with pytest.raises(ValueError):
            pool_limits(settings, unused)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            pool_timeout(settings, unused)  # type: ignore[arg-type]

    # 保留边界只来自 contracts schema（缺分组回落 schema 默认；坏值显式失败，不静默回落）
    assert retention_policy_from_settings(
        {
            "webchat": {
                "stream_replay_retention_hours": STREAM_REPLAY_RETENTION_HOURS,
                "notification_retention_days": 3,
                "history_retention_days": HISTORY_RETENTION_DAYS,
            }
        }
    ) == RetentionPolicy(STREAM_REPLAY_RETENTION_HOURS, 3, HISTORY_RETENTION_DAYS)
    assert retention_policy_from_settings(None) == RetentionPolicy(24, 30, 0), (
        "缺分组回落的是 contracts schema 默认（唯一来源）"
    )
    for bad in (
        {"webchat": {"notification_retention_days": 0}},
        {"webchat": {"stream_replay_retention_hours": 0}},
        {"webchat": {"history_retention_days": -1}},
        {"unknown_group": {}},
    ):
        with pytest.raises(PlatformSettingsError):
            retention_policy_from_settings(bad)


async def test_b09_global_admission_keeps_the_higher_revision_and_reads_settings_outside_the_lock(
    wiring_env: _WiringEnv,
) -> None:
    """[B-09] 低版本不覆盖全局容量；配置网络读取在行锁短事务外（行为 + 结构）。"""
    from muad_agent_runtime import main

    wiring_env.console.revision = 7
    wiring_env.console.document = _settings_document(tenant_limit=500, user_limit=4)
    async with main.lifespan(main.app):
        settings_client = main.app.state.platform_settings_client
        service = AdmissionPolicyService(get_session_factory, settings_client)
        policy = await service.resolve(tenant_id=wiring_env.tenant.tenant_id)
        assert policy.source_settings_revision == 7
        assert policy.limits.max_open_runs_per_tenant == 500
        assert policy.limits.max_open_runs_per_user == 4

        # 读到更低版本：退出事务后重取一次仍低 ⇒ 显式失败，绝不按旧阈值放行/覆盖
        wiring_env.console.revision = 3
        wiring_env.console.document = _settings_document(tenant_limit=100, user_limit=2)
        calls_before = len(wiring_env.console.calls)
        with pytest.raises(AppError):
            await service.resolve(tenant_id=wiring_env.tenant.tenant_id)
        assert len(wiring_env.console.calls) > calls_before, "低版本必须退出事务后重取一次"
        row = await _policy_row(wiring_env.tenant.tenant_id)
        assert (
            row.source_settings_revision,
            row.max_open_runs_per_tenant,
            row.max_open_runs_per_user,
        ) == (7, 500, 4), "低版本不得覆盖全局容量"

    # 结构断言：取配置不碰会话/事务，锁内不联网（配置网络读取在行锁短事务外）
    admission_source = (
        REPO_ROOT / "apps/agent-runtime/src/muad_agent_runtime/application/run_admission.py"
    ).read_text(encoding="utf-8")
    fetch_capacity = _function_node(admission_source, "fetch_capacity")
    assert "fetch_snapshot" in _called_names(fetch_capacity)
    assert "_session_factory_provider" not in ast.dump(fetch_capacity), "取配置必须在事务之外"
    lock_and_apply = _function_node(admission_source, "lock_and_apply_in")
    assert "fetch_snapshot" not in _called_names(lock_and_apply), "锁内不得联网读配置"

    main_source = (REPO_ROOT / "apps/agent-runtime/src/muad_agent_runtime/main.py").read_text(
        encoding="utf-8"
    )
    retention_loop = _function_node(main_source, "_retention_loop")
    sweep_blocks = [
        node
        for node in ast.walk(retention_loop)
        if isinstance(node, ast.AsyncWith) and "run_retention_sweep" in _called_names(node)
    ]
    assert sweep_blocks, "保留清理必须在真实事务内调用 run_retention_sweep"
    for block in sweep_blocks:
        assert "fetch_snapshot" not in _called_names(block), (
            "保留边界的网络读取必须在清理事务之外"
        )
