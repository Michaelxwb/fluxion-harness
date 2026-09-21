from __future__ import annotations

import uuid
from typing import Protocol


class McpCatalogCache(Protocol):
    async def invalidate(self, *, server_id: uuid.UUID, revision: int) -> None: ...


class NullMcpCatalogCache:
    async def invalidate(self, *, server_id: uuid.UUID, revision: int) -> None:
        del server_id, revision
