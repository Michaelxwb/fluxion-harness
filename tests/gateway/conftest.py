from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from muad_api.catalog import MessageCatalog
from muad_im_gateway.main import app

# 隔离 PostgreSQL 号位（B-267 等需要真实 PG 行事实的 Gateway 验收复用同一实现）。
from tests.async_tool_datastores import async_tool_datastore  # noqa: F401
from tests.internal_service import internal_service_token  # noqa: F401  (fixture 注册)


@pytest.fixture(scope="session")
def catalog() -> MessageCatalog:
    return app.state.message_catalog


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
