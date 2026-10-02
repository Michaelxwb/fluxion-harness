"""企微入站附件的需求级验收栈（TASK-010）。

与 `tests/acceptance/im_gateway` 同一套原语：真实 WS 探针 + 真实四进程 + 真实 PG/Redis +
真实共享 artifact store；额外的只有**真实 HTTP 媒体源**（真实 AES-256-CBC 密文）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from muad_common import SharedSettings

from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    BOT_SECRET,
    GatewayStack,
    clear_engine_caches,
    purge_tenant,
    require,
    start_gateway_stack,
    stop_gateway_stack,
)
from tests.e2e.wecom_media_server import MediaServer
from tests.e2e.wecom_probe_app import WeComProbe


@pytest.fixture(scope="module", autouse=True)
def isolate_engine_caches() -> Iterator[None]:
    yield
    clear_engine_caches()


@pytest.fixture(scope="module")
def media_server() -> Iterator[MediaServer]:
    """真实 HTTP 媒体源：网关子进程按回调里的 URL 直接访问它（同一主机）。"""
    server = MediaServer()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture(scope="module")
async def gateway_stack(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[GatewayStack]:
    settings = SharedSettings()
    require("DATABASE_URL", settings.database_url)
    require("REDIS_URL", settings.redis_url)

    clear_engine_caches()
    root = tmp_path_factory.mktemp("wecom-attachments-acceptance")
    probe = WeComProbe(expected_bots={BOT_ID: BOT_SECRET})
    await probe.start()
    stack, processes = start_gateway_stack(
        root, ws_probe_url=probe.ws_url, ws_ca_file=str(probe.cert_path)
    )
    stack.ws_probe = probe
    try:
        yield stack
    finally:
        stop_gateway_stack(processes)
        await purge_tenant()
        await probe.stop()
        clear_engine_caches()
