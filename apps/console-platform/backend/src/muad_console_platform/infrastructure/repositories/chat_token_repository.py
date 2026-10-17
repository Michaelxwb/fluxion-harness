"""用户 Webchat Token 与独立 CHAT 会话的仓储（设计 §3.3 `control.chat_access_token` / `chat_session`）。

- 只做**带租户谓词**的读写，不提交事务（提交/回滚由调用方决定）；归属与状态的事实由数据库
  partial unique 与 FK 兜底，本层不复制一套业务校验。
- `find_live_for_update` 取未撤销行并加 `FOR UPDATE`：调用方已按"用户→Token"固定锁序先锁
  `control.platform_user` 行，这里再锁 Token 行，使"每用户至多一个未撤销 Token"的读-改-写在
  锁内闭合（过期但未撤销的行同样是 live，必须先显式撤销再重签）。
- `revoke` 在**同一事务**撤销来源 Token 行与其全部活跃会话（设计 §3.4 API-03：重置/撤销时
  关联会话同事务失效），先前已撤销的会话保持原时刻，不被改写。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.auth import ChatAccessToken, ChatSession


class ChatTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_token_hash(self, token_hash: str) -> ChatAccessToken | None:
        """按摘要定位 Token 行（**不含租户谓词**：交换时浏览器只提交 Token，租户由记录导出）。"""
        row: ChatAccessToken | None = await self._session.scalar(
            select(ChatAccessToken).where(
                ChatAccessToken.token_hash == token_hash,
                ChatAccessToken.is_deleted.is_(False),
            )
        )
        return row

    async def find_by_id(self, token_id: uuid.UUID) -> ChatAccessToken | None:
        """按主键定位 Token 行：会话读取用它复核来源 Token 的当前状态。"""
        row: ChatAccessToken | None = await self._session.scalar(
            select(ChatAccessToken).where(
                ChatAccessToken.id == token_id,
                ChatAccessToken.is_deleted.is_(False),
            )
        )
        return row

    async def find_latest(self, tenant_id: str, user_id: uuid.UUID) -> ChatAccessToken | None:
        """版本基准行：最大 `revision`（撤销不删行，故可能是已撤销/已过期的历史行）。"""
        row: ChatAccessToken | None = await self._session.scalar(
            select(ChatAccessToken)
            .where(
                ChatAccessToken.tenant_id == tenant_id,
                ChatAccessToken.platform_user_id == user_id,
                ChatAccessToken.is_deleted.is_(False),
            )
            .order_by(ChatAccessToken.revision.desc())
            .limit(1)
        )
        return row

    async def find_live_for_update(self, tenant_id: str, user_id: uuid.UUID) -> ChatAccessToken | None:
        """当前未撤销行（含已过期未撤销者），行锁下读取。"""
        row: ChatAccessToken | None = await self._session.scalar(
            select(ChatAccessToken)
            .where(
                ChatAccessToken.tenant_id == tenant_id,
                ChatAccessToken.platform_user_id == user_id,
                ChatAccessToken.revoked_at.is_(None),
                ChatAccessToken.is_deleted.is_(False),
            )
            .with_for_update()
        )
        return row

    async def find_by_revision(
        self, tenant_id: str, user_id: uuid.UUID, revision: int
    ) -> ChatAccessToken | None:
        """目录版本行（`revision` 由 API-03 的 `expected_revision` 指定）。"""
        row: ChatAccessToken | None = await self._session.scalar(
            select(ChatAccessToken).where(
                ChatAccessToken.tenant_id == tenant_id,
                ChatAccessToken.platform_user_id == user_id,
                ChatAccessToken.revision == revision,
                ChatAccessToken.is_deleted.is_(False),
            )
        )
        return row

    async def add(self, row: ChatAccessToken) -> ChatAccessToken:
        self._session.add(row)
        await self._session.flush()
        return row

    async def revoke(self, row: ChatAccessToken, revoked_at: datetime) -> int:
        """撤销本行与其**全部活跃**会话；返回本次真正撤销的会话数（同事务，不提交）。"""
        row.revoked_at = revoked_at
        row.update_time = revoked_at
        result = await self._session.execute(
            update(ChatSession)
            .where(
                ChatSession.tenant_id == row.tenant_id,
                ChatSession.access_token_id == row.id,
                ChatSession.revoked_at.is_(None),
                ChatSession.is_deleted.is_(False),
            )
            .values(revoked_at=revoked_at, update_time=revoked_at)
        )
        await self._session.flush()
        return int(cast(CursorResult[Any], result).rowcount or 0)
