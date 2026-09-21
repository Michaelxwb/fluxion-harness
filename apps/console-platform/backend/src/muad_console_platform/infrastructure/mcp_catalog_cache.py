from __future__ import annotations

import uuid
from typing import Any


class RedisMcpCatalogCache:
    """删除 Runtime 侧 `mcp:tools:{server_id}:{revision}` 缓存（best-effort）。"""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def invalidate(self, *, server_id: uuid.UUID, revision: int) -> None:
        await self._client.delete(f"mcp:tools:{server_id}:{revision}")
