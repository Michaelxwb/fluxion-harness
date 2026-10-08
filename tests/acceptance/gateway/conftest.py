"""S-06/E-13/E-17 验收环境：task-schedule 真栈 + join 模型探针 + WS 网关 + 可断流中继。

不得 Mock 的真实边界：真实 Console/Runtime/Worker 进程（真实 HTTP/PG/Redis）、真实
Worker 执行 ASYNC JOIN Skill、真实 WeCom 协议 WS（官方 SDK）承载入站与出站、原消息回复探针。
网关进程经一个真实 TCP 中继连 Runtime —— 用例可在**不杀进程**的前提下断开在飞 SSE。

`join_stack` 复用 runtime 验收的既有夹具（task-schedule 进程栈 + gate 化 ASYNC Skill +
join 模型探针）；本夹具只在其上补：渠道身份绑定、WS 网关进程与断流中继。
"""

from __future__ import annotations

import os
import select
import socket
import threading
import urllib.parse
from collections.abc import AsyncIterator, Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import delete
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.acceptance.runtime.test_background_result_resume import join_stack as _join_stack
from tests.acceptance.task_schedule.environment import (
    BOT_ID,
    BOT_SECRET,
    INTERNAL_TOKEN,
    TENANT,
    LiveStack,
    ServiceProcess,
    clear_engine_caches,
    free_port,
    require,
    run_db,
)
from tests.e2e.wecom_probe_app import WeComProbe

WAITING_EXTERNAL_USER_ID = "e2e-waiting-ext"
WAITING_CHAT_ID = "e2e-waiting-chat"


class RuntimeProxy:
    """真实 TCP 中继：字节透传，可强制断开在飞连接（复现 SSE 断流，不杀 Runtime 进程）。"""

    def __init__(self, upstream_url: str) -> None:
        parts = urllib.parse.urlsplit(upstream_url)
        self._upstream = (parts.hostname or "127.0.0.1", parts.port or 80)
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self._server: socket.socket | None = None
        self._pairs: list[tuple[socket.socket, socket.socket]] = []
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", self.port))
        server.listen(64)
        server.settimeout(0.5)
        self._server = server
        self._thread = threading.Thread(target=self._accept_loop, name="runtime-proxy", daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        assert self._server is not None
        while not self._stopped.is_set():
            try:
                client, _ = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=self._relay, args=(client,), daemon=True).start()

    def _relay(self, client: socket.socket) -> None:
        try:
            upstream = socket.create_connection(self._upstream, timeout=5)
        except OSError:
            client.close()
            return
        with self._lock:
            self._pairs.append((client, upstream))
        try:
            while True:
                readable, _, _ = select.select([client, upstream], [], [], 1.0)
                for source, target in ((client, upstream), (upstream, client)):
                    if source not in readable:
                        continue
                    data = source.recv(65536)
                    if not data:
                        return
                    target.sendall(data)
        except OSError:
            return
        finally:
            for sock in (client, upstream):
                with suppress(OSError):
                    sock.close()
            with self._lock:
                with suppress(ValueError):
                    self._pairs.remove((client, upstream))

    def drop_active(self) -> None:
        """断开当前所有在飞连接（含 SSE）；监听仍在，重连立即可以建立。"""
        with self._lock:
            pairs = list(self._pairs)
        for client, upstream in pairs:
            for sock in (client, upstream):
                with suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)
                with suppress(OSError):
                    sock.close()

    def stop(self) -> None:
        self._stopped.set()
        if self._server is not None:
            with suppress(OSError):
                self._server.close()
        self.drop_active()
        if self._thread is not None:
            self._thread.join(timeout=5)


@dataclass
class WaitingGatewayStack:
    live: LiveStack
    gateway_url: str
    gateway_log: Path
    external_user_id: str
    chat_id: str
    ws_probe: WeComProbe
    runtime_proxy: RuntimeProxy

    def service_headers(self) -> dict[str, str]:
        return {"X-Tenant-Id": TENANT, "X-Internal-Service": INTERNAL_TOKEN}


@pytest.fixture(scope="module", autouse=True)
def isolate_engine_caches() -> Iterator[None]:
    yield
    clear_engine_caches()


async def _seed_binding(
    factory: async_sessionmaker[AsyncSession], live: LiveStack
) -> None:
    from muad_console_platform.infrastructure.models.channel import BotAccount, ChannelIdentity

    async with factory() as session, session.begin():
        await session.execute(
            delete(ChannelIdentity).where(
                ChannelIdentity.tenant_id == TENANT,
                ChannelIdentity.external_user_id == WAITING_EXTERNAL_USER_ID,
            )
        )
        bot = (
            await session.scalars(
                sa_select(BotAccount).where(
                    BotAccount.tenant_id == TENANT, BotAccount.bot_id == BOT_ID
                )
            )
        ).one()
        session.add(
            ChannelIdentity(
                tenant_id=TENANT,
                channel="WECOM",
                identity_key=f"WECOM:{BOT_ID}:{WAITING_EXTERNAL_USER_ID}",
                external_user_id=WAITING_EXTERNAL_USER_ID,
                bot_account_id=bot.id,
                platform_user_id=live.platform_user_id,
            )
        )


async def _delete_binding(factory: async_sessionmaker[AsyncSession]) -> None:
    from muad_console_platform.infrastructure.models.channel import ChannelIdentity

    async with factory() as session, session.begin():
        await session.execute(
            delete(ChannelIdentity).where(
                ChannelIdentity.tenant_id == TENANT,
                ChannelIdentity.external_user_id == WAITING_EXTERNAL_USER_ID,
            )
        )


def _gateway_env(live: LiveStack, probe: WeComProbe, runtime_url: str) -> dict[str, str]:
    from muad_common import SharedSettings

    settings = SharedSettings()
    cache = Path(live.artifact_root) / "gateway-waiting-skill-cache"
    cache.mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "DATABASE_URL": settings.require_database_url(),
        "REDIS_URL": settings.require_redis_url(),
        "DEFAULT_TENANT_ID": TENANT,
        "INTERNAL_SERVICE_TOKEN": INTERNAL_TOKEN,
        "CONSOLE_PLATFORM_URL": live.console_url,
        "AGENT_RUNTIME_URL": runtime_url,
        "WECOM_WS_URL": probe.ws_url,
        "WECOM_WS_CA_FILE": str(probe.cert_path),
        "ARTIFACT_ROOT": str(live.artifact_root),
        "SKILL_CACHE_ROOT": str(cache),
    }


@pytest.fixture(scope="module", name="gateway_stack")
async def gateway_stack(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[WaitingGatewayStack]:
    from muad_common import SharedSettings

    settings = SharedSettings()
    require("DATABASE_URL", settings.database_url)
    require("REDIS_URL", settings.redis_url)

    generator = _join_stack.__wrapped__(tmp_path_factory)  # type: ignore[attr-defined]
    live: LiveStack = next(generator)
    probe = WeComProbe(expected_bots={BOT_ID: BOT_SECRET})
    await probe.start()
    proxy = RuntimeProxy(live.runtime_url)
    proxy.start()
    processes: list[ServiceProcess] = []
    try:
        run_db(lambda factory: _seed_binding(factory, live))
        log_path = Path(live.artifact_root) / "gateway-waiting.log"
        gateway = ServiceProcess(
            "gateway-waiting",
            "muad_im_gateway.main",
            free_port(),
            _gateway_env(live, probe, proxy.url),
            log_path,
        )
        processes.append(gateway)
        gateway.start()
        yield WaitingGatewayStack(
            live=live,
            gateway_url=gateway.url,
            gateway_log=log_path,
            external_user_id=WAITING_EXTERNAL_USER_ID,
            chat_id=WAITING_CHAT_ID,
            ws_probe=probe,
            runtime_proxy=proxy,
        )
    finally:
        for process in reversed(processes):
            process.stop()
        with suppress(Exception):
            run_db(lambda factory: _delete_binding(factory))
        proxy.stop()
        await probe.stop()
        with suppress(StopIteration):
            next(generator)
