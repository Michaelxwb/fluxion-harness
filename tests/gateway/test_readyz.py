from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fakes import FakeConsoleClient, StubConsole
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from muad_api import AppError, StartupValidationError
from muad_api.error_codes import ErrorCode
from muad_contracts import BotSnapshotResponse
from muad_im_core import BotSnapshotCache
from muad_im_gateway.api.health import dedupe_mode
from muad_im_gateway.application.console_client import ConsoleClient
from muad_im_gateway.application.runtime_client import RuntimeClient
from muad_im_gateway.channels.base import ChannelRegistry
from muad_im_gateway.channels.fake import FakeChannelAdapter
from muad_im_gateway.infrastructure.dedupe import (
    InMemoryDedupeStore,
    NullDedupeStore,
    RedisDedupeStore,
)
from muad_im_gateway.main import app


class _StubRedis:
    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        return True

    async def aclose(self) -> None:
        return None


class _BackingOffAdapter(FakeChannelAdapter):
    """连接管理器已初始化但 bot 未 CONNECTED（单 bot 退避中）。"""

    @property
    def degraded_bots(self) -> dict[str, str]:
        return {"bot-1": "BACKOFF"}


@asynccontextmanager
async def api_client(
    registry: ChannelRegistry,
    dedupe: Any,
    snapshot: BotSnapshotCache,
) -> AsyncIterator[AsyncClient]:
    # 探针由 api-kit 原语注册（无 DI），依赖来自 app.state —— 与真实 lifespan 的装配方式一致
    app.state.registry = registry
    app.state.dedupe_store = dedupe
    app.state.bot_snapshot = snapshot
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        # 这些是**本探针**读取的全部 app.state（含多 worker 改造新增的 stream 依赖）：
        # 不清理会让上一个用例的装配残留（真实 streams 对象 + stream_reliant=True）改变
        # 本用例的 /readyz 结论（整跑红、单跑绿）。
        for name in ("registry", "dedupe_store", "bot_snapshot", "stream_reliant", "delivery_streams"):
            setattr(app.state, name, None)


async def _registry(*, started: bool) -> ChannelRegistry:
    registry = ChannelRegistry()
    registry.register(FakeChannelAdapter())
    if started:
        await registry.start_all()
    return registry


async def test_healthz_reports_ok() -> None:
    snapshot = BotSnapshotCache(FakeConsoleClient(), tenant_id="t1")
    async with api_client(await _registry(started=False), InMemoryDedupeStore(), snapshot) as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ok"}


async def test_readyz_ready_with_started_adapter_and_console() -> None:
    console = FakeConsoleClient()
    console.bot_snapshot = BotSnapshotResponse(revision="rev-1")
    snapshot = BotSnapshotCache(console, tenant_id="t1")
    await snapshot.refresh()
    async with api_client(await _registry(started=True), InMemoryDedupeStore(), snapshot) as client:
        response = await client.get("/readyz")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data == {
        "status": "ready",
        "adapters": {"WECOM": True},
        "degraded_bots": {},
        "dedupe": "memory",
        "bots_revision": "rev-1",
    }


async def test_readyz_503_without_started_adapter() -> None:
    console = FakeConsoleClient()
    snapshot = BotSnapshotCache(console, tenant_id="t1")
    await snapshot.refresh()
    async with api_client(await _registry(started=False), InMemoryDedupeStore(), snapshot) as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["data"]["adapters"] == {"WECOM": False}


async def test_readyz_stays_ready_while_bot_backs_off() -> None:
    """设计 §4.1：manager 已初始化即就绪，单 bot 退避只在 detail 记 degraded。"""
    console = FakeConsoleClient()
    console.bot_snapshot = BotSnapshotResponse(revision="rev-1")
    snapshot = BotSnapshotCache(console, tenant_id="t1")
    await snapshot.refresh()
    registry = ChannelRegistry()
    registry.register(_BackingOffAdapter())
    await registry.start_all()
    async with api_client(registry, InMemoryDedupeStore(), snapshot) as client:
        response = await client.get("/readyz")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "ready"
    assert data["degraded_bots"] == {"bot-1": "BACKOFF"}


async def test_readyz_503_when_console_unreachable_and_cache_empty() -> None:
    console = FakeConsoleClient()
    console.bots_error = AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    snapshot = BotSnapshotCache(console, tenant_id="t1")
    await snapshot.refresh()
    async with api_client(await _registry(started=True), InMemoryDedupeStore(), snapshot) as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["data"]["bots_revision"] is None


async def test_readyz_stays_ready_with_stale_cache_when_console_unreachable() -> None:
    console = FakeConsoleClient()
    console.bot_snapshot = BotSnapshotResponse(revision="rev-2")
    snapshot = BotSnapshotCache(console, tenant_id="t1")
    await snapshot.refresh()
    console.bots_error = AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    await snapshot.refresh()

    async with api_client(await _registry(started=True), InMemoryDedupeStore(), snapshot) as client:
        response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["data"]["bots_revision"] == "rev-2"


def test_dedupe_mode_maps_concrete_stores() -> None:
    assert dedupe_mode(NullDedupeStore()) == "disabled"
    assert dedupe_mode(InMemoryDedupeStore()) == "memory"
    assert dedupe_mode(RedisDedupeStore(_StubRedis())) == "redis"  # type: ignore[arg-type]


def test_dedupe_mode_maps_unknown_store_to_disabled() -> None:
    class _CustomStore:
        """既不是 Redis 也不是内存实现的自建 store：`dedupe_mode` 必须报 `disabled`。"""

        async def reserve(self, key: str, ttl_sec: int) -> str | None:
            return "owner"

        async def aclose(self) -> None:
            return None

    assert dedupe_mode(_CustomStore()) == "disabled"


# ---------------------------------------------------------------------------
# B-107: 启动、就绪与关闭语义（真实 Gateway lifespan + 真实 HTTP 探针）
#
# 渠道长连接/secret 已迁到 Connector（TASK-008）：Gateway 是请求面，注册表只有 WEBCHAT
# 与配置驱动的 webhook 通道。degraded_bots/reconnect 这类连接语义在 Connector 侧验证。
# ---------------------------------------------------------------------------

B107_WEBCHAT_ENTRY = "entry-b107-webchat"
BOTS_PATH = "/internal/channel/bots"
HTTP_TIMEOUT_SEC = 5.0
GATEWAY_BOOT_TIMEOUT_SEC = 30.0
GATEWAY_DRAIN_TIMEOUT_SEC = 10.0
# 关闭后不得残留的任务：Gateway 自身与官方 SDK 的内部任务
GATEWAY_CORO_MARKERS = ("/muad_im_gateway/", "/aibot/")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _bots_envelope(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "code": "0",
        "msg": "成功",
        "data": {
            "revision": "rev-b107",
            "items": items,
            "page": 1,
            "page_size": 20,
            "total": len(items),
        },
    }


def _bot_payload(bot_id: str, secret: str | None, *, channel: str = "WEBCHAT") -> dict[str, Any]:
    return {
        "bot_account_id": str(uuid4()),
        "bot_id": bot_id,
        "channel": channel,
        "secret": secret,
        "agent_id": str(uuid4()),
        "enabled": True,
    }


def _install_gateway_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    console_url: str,
    artifact_root: Path,
) -> None:
    """真实配置入口：Gateway 只从环境读配置，lifespan 启动时快照这些值。"""
    artifact_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("CONSOLE_PLATFORM_URL", console_url)
    # Redis 指向不可达端口：本文件的用例不投递/不消费 Stream；入站消费任务按退避重试，
    # 绝不碰共享 dev Redis 的真实事件。
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("GATEWAY_WEBHOOK_CHANNELS", "")


async def _wait_until(
    predicate: Callable[[], bool], *, what: str, timeout: float = GATEWAY_BOOT_TIMEOUT_SEC
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(what)


def _gateway_tasks() -> set[str]:
    """当前事件循环中未完成的 Gateway / 官方 SDK 任务（按协程文件归属识别）。"""
    found: set[str] = set()
    for task in asyncio.all_tasks():
        code = getattr(task.get_coro(), "cr_code", None)
        filename = str(getattr(code, "co_filename", ""))
        if any(marker in filename for marker in GATEWAY_CORO_MARKERS):
            found.add(f"{task.get_name()}@{Path(filename).name}")
    return found


async def _wait_gateway_tasks_drained(baseline: set[str]) -> set[str]:
    """关闭后等待 Gateway/SDK 任务收尾（有界）；返回仍未结束的任务。"""
    deadline = asyncio.get_running_loop().time() + GATEWAY_DRAIN_TIMEOUT_SEC
    while asyncio.get_running_loop().time() < deadline:
        remaining = _gateway_tasks() - baseline
        if not remaining:
            return set()
        await asyncio.sleep(0.05)
    return _gateway_tasks() - baseline


@asynccontextmanager
async def running_gateway() -> AsyncIterator[httpx.AsyncClient]:
    """真实 Gateway：uvicorn 真实 socket（真实 HTTP 探针）+ 真实 lifespan。"""
    config = uvicorn.Config(app, host="127.0.0.1", port=_free_port(), log_level="error")
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        await _wait_until(lambda: server.started, what="gateway 未在超时内监听")
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{config.port}", timeout=HTTP_TIMEOUT_SEC
        ) as client:
            yield client
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=GATEWAY_DRAIN_TIMEOUT_SEC)


async def _wait_readyz_degraded(client: httpx.AsyncClient, expected: set[str]) -> dict[str, Any]:
    """等待首次连接尝试收敛：readyz 200 且 degraded 恰为目标集合。"""
    deadline = asyncio.get_running_loop().time() + GATEWAY_BOOT_TIMEOUT_SEC
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get("/readyz")
        data = response.json()["data"]
        if response.status_code == 200 and set(data.get("degraded_bots") or {}) == expected:
            return data
        await asyncio.sleep(0.1)
    raise AssertionError(f"readyz 未收敛到 degraded={expected}")


async def test_b107_liveness_only_and_missing_console_is_not_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """healthz 仅存活；缺启动必需条件（Console 不可达且无完整快照）readyz=503。"""
    _install_gateway_env(
        monkeypatch,
        console_url=f"http://127.0.0.1:{_free_port()}",  # 不可达的 Console
        artifact_root=tmp_path / "artifacts",
    )
    baseline = _gateway_tasks()
    async with running_gateway() as client:
        healthz = await client.get("/healthz")
        assert healthz.status_code == 200
        assert healthz.json()["data"] == {"status": "ok"}

        readyz = await client.get("/readyz")
        data = readyz.json()["data"]
        assert readyz.status_code == 503
        assert data["failed"] == ["console"]  # 只有整体必需条件缺失才 503
        assert data["adapters"] == {"WEBCHAT": True}  # 请求面适配器已初始化
        assert data["bots_revision"] is None
    assert await _wait_gateway_tasks_drained(baseline) == set()


async def test_b107_ready_with_snapshot_and_console_outage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """快照可用时 Console 短暂不可达仍 ready；关闭后适配器停止、无遗留任务。"""
    console = StubConsole()
    console.start()
    console.json_response(
        BOTS_PATH,
        _bots_envelope([_bot_payload(B107_WEBCHAT_ENTRY, None)]),
    )
    _install_gateway_env(monkeypatch, console_url=console.url, artifact_root=tmp_path / "artifacts")
    baseline = _gateway_tasks()
    try:
        async with running_gateway() as client:
            await _wait_until(
                lambda: (app.state.bot_snapshot is not None)
                and app.state.bot_snapshot.is_ready(),
                what="入口快照未就绪",
            )
            readyz = await client.get("/readyz")
            assert readyz.status_code == 200, readyz.text
            data = readyz.json()["data"]
            assert data["status"] == "ready"
            assert data["adapters"] == {"WEBCHAT": True}
            assert data["degraded_bots"] == {}
            assert data["bots_revision"] == "rev-b107"

            # 设计 §4.1：Console 短暂不可达但有完整快照时仍可服务
            console.stop()
            outage = await client.get("/readyz")
            assert outage.status_code == 200
            assert app.state.registry.adapter_states == {"WEBCHAT": True}

        assert app.state.registry.adapter_states == {"WEBCHAT": False}
        assert await _wait_gateway_tasks_drained(baseline) == set()
    finally:
        console.stop()


async def test_b107_restart_leaves_no_tasks_or_connections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """连续两次 lifespan：每次启动/关闭都不遗留 Gateway 生命周期任务。"""
    console = StubConsole()
    console.start()
    console.json_response(BOTS_PATH, _bots_envelope([_bot_payload(B107_WEBCHAT_ENTRY, None)]))
    _install_gateway_env(monkeypatch, console_url=console.url, artifact_root=tmp_path / "artifacts")
    baseline = _gateway_tasks()
    try:
        for _ in range(2):
            async with running_gateway() as client:
                assert (await client.get("/readyz")).status_code == 200
            assert await _wait_gateway_tasks_drained(baseline) == set()
    finally:
        console.stop()


async def test_b107_gateway_requests_no_bot_secrets_and_needs_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """请求面不需要 bot secret：快照请求带 `include_secret=false`，没有 secret 也能就绪。"""
    console = StubConsole()
    console.start()
    seen: dict[str, str] = {}

    async def _bots(request: Any) -> Any:
        seen.update(dict(request.query_params))
        return JSONResponse(
            status_code=200, content=_bots_envelope([_bot_payload(B107_WEBCHAT_ENTRY, None)])
        )

    console.handlers[BOTS_PATH] = _bots
    _install_gateway_env(monkeypatch, console_url=console.url, artifact_root=tmp_path / "artifacts")
    try:
        async with running_gateway() as client:
            assert (await client.get("/healthz")).status_code == 200
            await _wait_until(
                lambda: (app.state.bot_snapshot is not None)
                and app.state.bot_snapshot.is_ready(),
                what="入口快照未就绪",
            )
            readyz = await client.get("/readyz")
            assert readyz.status_code == 200, readyz.text
            assert readyz.json()["data"]["degraded_bots"] == {}
        assert seen.get("channels") == "WEBCHAT"
        assert seen.get("include_secret") == "false"
        assert all(item.secret is None for item in app.state.bot_snapshot.items)
    finally:
        console.stop()


async def test_b107_startup_validation_failure_creates_no_resources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """配置/挂载校验失败 fail fast：不创建运行时资源、不遗留任务。"""
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "absent-artifacts"))  # 未挂载
    marker = ChannelRegistry()  # 启动前的状态哨兵：失败启动不得覆盖它
    app.state.registry = marker
    baseline = _gateway_tasks()
    with pytest.raises(StartupValidationError):
        async with app.router.lifespan_context(app):
            pytest.fail("lifespan 未按 fail fast 终止")

    assert app.state.registry is marker
    assert await _wait_gateway_tasks_drained(baseline) == set()


async def test_b107_failed_initialization_releases_created_resources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """初始化中途失败：已创建的真实资源被释放，且不发布半初始化状态。"""
    console = StubConsole()
    console.start()
    console.json_response(BOTS_PATH, _bots_envelope([_bot_payload(B107_WEBCHAT_ENTRY, None)]))
    _install_gateway_env(
        monkeypatch,
        console_url=console.url,
        artifact_root=tmp_path / "artifacts",
    )

    released: list[str] = []
    for name, owner in (("console", ConsoleClient), ("runtime", RuntimeClient)):
        original = owner.aclose

        async def aclose(self: Any, _name: str = name, _original: Any = original) -> None:
            released.append(_name)
            await _original(self)

        monkeypatch.setattr(owner, "aclose", aclose)

    async def failing_refresh(_: BotSnapshotCache) -> None:
        raise RuntimeError("b107 注入的启动期故障")

    monkeypatch.setattr(BotSnapshotCache, "refresh", failing_refresh)
    marker = ChannelRegistry()  # 启动前的状态哨兵：失败启动不得覆盖它
    app.state.registry = marker
    baseline = _gateway_tasks()
    try:
        with pytest.raises(RuntimeError, match="b107"):
            async with app.router.lifespan_context(app):
                pytest.fail("初始化失败未向上抛出")
    finally:
        console.stop()

    assert sorted(released) == ["console", "runtime"]
    assert app.state.registry is marker
    assert await _wait_gateway_tasks_drained(baseline) == set()
