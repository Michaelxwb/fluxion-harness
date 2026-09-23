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
from httpx import ASGITransport, AsyncClient
from muad_api import AppError, StartupValidationError
from muad_api.error_codes import ErrorCode
from muad_contracts import BotSnapshotResponse
from muad_im_gateway.api.health import dedupe_mode
from muad_im_gateway.application.bot_snapshot import BotSnapshotCache
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

from tests.e2e.wecom_probe_app import WeComProbe


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
        for name in ("registry", "dedupe_store", "bot_snapshot"):
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
        async def set_if_absent(self, key: str, ttl_sec: int) -> bool:
            return False

        async def aclose(self) -> None:
            return None

    assert dedupe_mode(_CustomStore()) == "disabled"


# ---------------------------------------------------------------------------
# B-107: 启动、就绪与关闭语义（真实 Gateway lifespan + 真实 HTTP 探针 + 真实 WS 连接管理器）
# ---------------------------------------------------------------------------

B107_OK_BOT = "bot-b107-ok"
B107_REJECTED_BOT = "bot-b107-rejected"
B107_SECRETLESS_BOT = "bot-b107-secretless"
B107_SECRET = "b107-secret"
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


def _bot_payload(bot_id: str, secret: str | None) -> dict[str, Any]:
    return {
        "bot_account_id": str(uuid4()),
        "bot_id": bot_id,
        "secret": secret,
        "agent_id": str(uuid4()),
        "enabled": True,
    }


def _install_gateway_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    console_url: str,
    artifact_root: Path,
    probe: WeComProbe | None = None,
) -> None:
    """真实配置入口：Gateway 只从环境读配置，lifespan 启动时快照这些值。"""
    artifact_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("CONSOLE_PLATFORM_URL", console_url)
    monkeypatch.setenv("CHANNEL_PROBE_URL", "")  # 保持真实 WS 渠道适配器
    monkeypatch.setenv("WECOM_WS_URL", probe.ws_url if probe is not None else "")
    monkeypatch.setenv("WECOM_WS_CA_FILE", str(probe.cert_path) if probe is not None else "")


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
    probe = WeComProbe(expected_bots={})
    await probe.start()
    _install_gateway_env(
        monkeypatch,
        console_url=f"http://127.0.0.1:{_free_port()}",  # 不可达的 Console
        artifact_root=tmp_path / "artifacts",
        probe=probe,
    )
    baseline = _gateway_tasks()
    try:
        async with running_gateway() as client:
            healthz = await client.get("/healthz")
            assert healthz.status_code == 200
            assert healthz.json()["data"] == {"status": "ok"}

            readyz = await client.get("/readyz")
            data = readyz.json()["data"]
            assert readyz.status_code == 503
            assert data["failed"] == ["console"]  # 只有整体必需条件缺失才 503
            assert data["adapters"] == {"WECOM": True}  # 连接管理器已初始化
            assert data["bots_revision"] is None
    finally:
        await probe.stop()
    assert await _wait_gateway_tasks_drained(baseline) == set()


async def test_b107_ready_with_degraded_bots_and_stale_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """单 bot 故障只记 degraded 且不停止其他 bot；不要求全部 CONNECTED；快照可用时 Console 不可达仍 ready。"""
    probe = WeComProbe(expected_bots={B107_OK_BOT: B107_SECRET})
    probe.reject_bot_ids.add(B107_REJECTED_BOT)
    await probe.start()
    console = StubConsole()
    console.start()
    console.json_response(
        BOTS_PATH,
        _bots_envelope(
            [
                _bot_payload(B107_OK_BOT, B107_SECRET),
                _bot_payload(B107_REJECTED_BOT, B107_SECRET),
                _bot_payload(B107_SECRETLESS_BOT, None),
            ]
        ),
    )
    _install_gateway_env(
        monkeypatch,
        console_url=console.url,
        artifact_root=tmp_path / "artifacts",
        probe=probe,
    )
    baseline = _gateway_tasks()
    try:
        async with running_gateway() as client:
            data = await _wait_readyz_degraded(client, {B107_REJECTED_BOT, B107_SECRETLESS_BOT})
            assert data["status"] == "ready"
            assert data["adapters"] == {"WECOM": True}
            assert data["bots_revision"] == "rev-b107"
            # 好 bot 经真实 socket 完成认证：没有被其他 bot 的故障停掉
            await _wait_until(
                lambda: any(
                    (frame.get("body") or {}).get("bot_id") == B107_OK_BOT
                    for frame in probe.frames_of("aibot_subscribe")
                ),
                what="好 bot 未在真实 WS 上完成认证",
            )
            # 设计 §4.1：Console 短暂不可达但有完整快照时仍可服务
            console.stop()
            outage = await client.get("/readyz")
            assert outage.status_code == 200
            assert app.state.registry.adapter_states == {"WECOM": True}

        # 关闭语义：连接管理器停止、真实连接关闭、无遗留任务
        assert app.state.registry.adapter_states == {"WECOM": False}
        await _wait_until(
            lambda: all(socket_.close_code is not None for socket_ in probe.connections),
            what="关闭后真实 WS 连接未释放",
            timeout=GATEWAY_DRAIN_TIMEOUT_SEC,
        )
        assert await _wait_gateway_tasks_drained(baseline) == set()
    finally:
        console.stop()
        await probe.stop()


async def test_b107_reconnect_then_shutdown_leaves_no_tasks_or_connections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """断线重连后关闭：上一轮连接的 SDK 内部任务必须被取消，真实连接全部释放。"""
    probe = WeComProbe(expected_bots={B107_OK_BOT: B107_SECRET})
    await probe.start()
    console = StubConsole()
    console.start()
    console.json_response(BOTS_PATH, _bots_envelope([_bot_payload(B107_OK_BOT, B107_SECRET)]))
    _install_gateway_env(
        monkeypatch,
        console_url=console.url,
        artifact_root=tmp_path / "artifacts",
        probe=probe,
    )
    baseline = _gateway_tasks()
    try:
        async with running_gateway() as client:
            await _wait_until(
                lambda: len(probe.connections) == 1, what="bot 未在真实 WS 上建立连接"
            )
            await probe.drop_connection(B107_OK_BOT)
            await _wait_until(
                lambda: len(probe.connections) > 1, what="断线后未重连", timeout=GATEWAY_BOOT_TIMEOUT_SEC
            )
            assert (await client.get("/readyz")).status_code == 200

        # 关闭不遗留任务/连接：旧连接与新连接的 SDK 任务都要收尾
        assert await _wait_gateway_tasks_drained(baseline) == set()
        await _wait_until(
            lambda: all(socket_.close_code is not None for socket_ in probe.connections),
            what="关闭后真实 WS 连接未释放",
            timeout=GATEWAY_DRAIN_TIMEOUT_SEC,
        )
    finally:
        console.stop()
        await probe.stop()


async def test_b107_all_bot_secrets_missing_is_not_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """全部 bot 的 secret 缺失 = 整体必需启动条件缺失 → readyz=503（failed 含 adapters）。"""
    console = StubConsole()
    console.start()
    console.json_response(BOTS_PATH, _bots_envelope([_bot_payload(B107_SECRETLESS_BOT, None)]))
    _install_gateway_env(
        monkeypatch,
        console_url=console.url,
        artifact_root=tmp_path / "artifacts",
    )
    try:
        async with running_gateway() as client:
            assert (await client.get("/healthz")).status_code == 200
            readyz = await client.get("/readyz")
            assert readyz.status_code == 503
            assert readyz.json()["data"]["failed"] == ["adapters"]
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
    console.json_response(BOTS_PATH, _bots_envelope([_bot_payload(B107_OK_BOT, B107_SECRET)]))
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
