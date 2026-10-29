"""CHAT 会话的 Redis 权威副本维护：授权变更后刷新/撤销，避免缓存放行旧权限。"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common.session_store import (
    RedisSessionStore,
    SessionAgent,
    SessionStoreUnavailable,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.auth import ChatSession
from ..infrastructure.repositories.agent_access_grant_repository import AgentAccessGrantRepository


class ChatSessionDirectory:
    """把当前 Agent 授权写回该用户所有活跃 CHAT 会话的 Redis 记录。"""

    def __init__(
        self, session: AsyncSession, *, session_store: RedisSessionStore | None
    ) -> None:
        self._session = session
        self._store = session_store
        self._grants = AgentAccessGrantRepository(session)

    async def _hashes(self, tenant_id: str, user_id: uuid.UUID) -> tuple[str, ...]:
        rows = await self._session.scalars(
            select(ChatSession.token_hash).where(
                ChatSession.tenant_id == tenant_id,
                ChatSession.platform_user_id == user_id,
                ChatSession.revoked_at.is_(None),
                ChatSession.is_deleted.is_(False),
            )
        )
        return tuple(str(value) for value in rows)

    async def refresh_user(self, tenant_id: str, user_id: uuid.UUID) -> None:
        """授权增加/减少后同步活跃会话；无 Redis/无会话时是空操作。"""
        if self._store is None:
            return
        hashes = await self._hashes(tenant_id, user_id)
        if not hashes:
            return
        rows = await self._grants.list_authorized_agents(tenant_id, user_id)
        agents = tuple(
            SessionAgent(agent_id=agent_id, key=key, name=name)
            for agent_id, key, name in rows
        )
        now = datetime.now(UTC)
        try:
            for token_hash in hashes:
                record = await self._store.read(token_hash)
                if record is None:
                    continue
                await self._store.write(token_hash, replace(record, agents=agents), now=now)
        except SessionStoreUnavailable as exc:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc

    async def invalidate_user(self, tenant_id: str, user_id: uuid.UUID) -> None:
        """停用/删除用户等强失效：删除该用户所有活跃会话的 Redis 副本。"""
        if self._store is None:
            return
        hashes = await self._hashes(tenant_id, user_id)
        try:
            for token_hash in hashes:
                await self._store.delete(token_hash)
        except SessionStoreUnavailable as exc:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
