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
    reset_redis_state,
    start_gateway_stack,
    stop_gateway_stack,
)
from tests.e2e.wecom_probe_app import WeComProbe


@pytest.fixture(scope="module", autouse=True)
def isolate_engine_caches() -> Iterator[None]:
    yield
    clear_engine_caches()


@pytest.fixture(scope="module", autouse=True)
def isolate_stack_redis() -> None:
    """每个模块从干净的栈级 Redis 工作状态开始（Stream/去重/回复/租约）。

    Connector→Gateway 的入站是**异步交接**（Connector 写 `im.inbound`，Gateway 的消费组
    读）：Stream、消费组与去重键都活在同一个 Redis DB 里。上一模块留下的事件（teardown 前
    推送的，或只写不读的 connector 原语套件写入的）会被本模块的 Gateway 当成新消息消费，
    表现为「单跑绿、整跑红」；`test_connector_primitives` 的「sweep 后 `im.inbound:0` 为空」
    也会被上一模块的残留打破。PG 每个栈都清，Redis 的栈级工作键同样在模块边界清。
    """
    reset_redis_state()


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
