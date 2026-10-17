"""独立 CHAT 会话的仓储（设计 §3.3 `control.chat_session`）。

- 只做读写，**不提交事务**（提交/回滚由调用方决定）；会话原文只进 HttpOnly Cookie，库里只有
  SHA-256 摘要，"摘要唯一且每用户可并存多条会话"由 0024 的 partial unique 兜底。
- `find_by_token_hash` 按摘要定位（**不含租户谓词**：Cookie 里只有会话原文，租户/用户由会话行导出）。
- `revoke` 只改本行（登出只撤本会话）；来源 Token 的**关联批量撤销**由
  `ChatTokenRepository.revoke` 在同一事务完成，本层不复制那套语义。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.auth import ChatSession


class ChatSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_token_hash(self, token_hash: str) -> ChatSession | None:
        """按摘要定位会话行（独立随机会话 Token 的 SHA-256 小写十六进制摘要）。"""
        row: ChatSession | None = await self._session.scalar(
            select(ChatSession).where(
                ChatSession.token_hash == token_hash,
                ChatSession.is_deleted.is_(False),
            )
        )
        return row

    async def add(self, row: ChatSession) -> ChatSession:
        self._session.add(row)
        await self._session.flush()
        return row

    async def revoke(self, row: ChatSession, revoked_at: datetime) -> None:
        """撤销本会话并留 `update_time` 痕迹；调用方负责幂等判定（已撤销的行不重复改写）。"""
        row.revoked_at = revoked_at
        row.update_time = revoked_at
        await self._session.flush()
