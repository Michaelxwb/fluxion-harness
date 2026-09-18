from __future__ import annotations

import uuid
from typing import Any

from .key import platform_sessions_index_key


class RedisPlatformSessionInvalidator:
    """基于 Set 索引清理平台 Session；禁止 KEYS/SCAN 或通配符删除。"""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def clear_platform(self, *, tenant_id: str, platform_id: uuid.UUID) -> int:
        index_key = platform_sessions_index_key(tenant_id=tenant_id, platform_id=str(platform_id))
        members = await self._client.smembers(index_key)
        keys = [member.decode() if isinstance(member, bytes) else str(member) for member in members]
        if keys:
            await self._client.delete(*keys)
        await self._client.delete(index_key)
        return len(keys)
