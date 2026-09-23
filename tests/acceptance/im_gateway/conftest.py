"""IM Gateway 基础验收环境 fixtures：真实进程 + 真实依赖（PG / Redis / 探针）。"""

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
from tests.e2e.wecom_probe_app import WeComProbe


@pytest.fixture(scope="module", autouse=True)
def isolate_engine_caches() -> Iterator[None]:
    yield
    clear_engine_caches()


@pytest.fixture(scope="module")
async def gateway_stack(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[GatewayStack]:
    settings = SharedSettings()
    require("DATABASE_URL", settings.database_url)
    require("REDIS_URL", settings.redis_url)

    clear_engine_caches()
    root = tmp_path_factory.mktemp("im-gateway-acceptance")
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
