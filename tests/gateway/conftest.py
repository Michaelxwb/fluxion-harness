from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from muad_api.catalog import MessageCatalog
from muad_im_gateway.main import app


@pytest.fixture(scope="session")
def catalog() -> MessageCatalog:
    return app.state.message_catalog


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
