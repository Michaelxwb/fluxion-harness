from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from muad_api.catalog import MessageCatalog
from muad_im_gateway.main import app

# 隔离 PostgreSQL 号位（B-267 等需要真实 PG 行事实的 Gateway 验收复用同一实现）。
from tests.async_tool_datastores import async_tool_datastore  # noqa: F401
from tests.internal_service import internal_service_token  # noqa: F401  (fixture 注册)


@pytest.fixture(autouse=True)
def _reset_gateway_session_store() -> None:
    """每个用例默认走 Console 主体门控；Redis 用例在自身 lifespan/状态注入里显式设置。"""
    app.state.session_store = None


@pytest.fixture(autouse=True)
def _isolate_gateway_channel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gateway 测试不继承开发机 `.env` 的通道拓扑。

    本机 `.env` 常为联调设置 `GATEWAY_ROUTED_CHANNELS=WECOM`；测试若同时声明 webhook
    WECOM 会触发重复适配器，要求 WebChat-only 的 readyz/registry 用例也会被误判。
    需要通道的用例在自身用例内显式 setenv。
    """
    monkeypatch.setenv("GATEWAY_ROUTED_CHANNELS", "")
    monkeypatch.setenv("GATEWAY_WEBHOOK_CHANNELS", "")


@pytest.fixture(scope="session")
def catalog() -> MessageCatalog:
    return app.state.message_catalog


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
