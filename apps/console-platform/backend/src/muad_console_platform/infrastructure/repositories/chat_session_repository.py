"""独立 CHAT 会话的仓储（设计 §3.3 `control.chat_session`）。

- 只做读写，**不提交事务**（提交/回滚由调用方决定）；会话原文只进 HttpOnly Cookie，库里只有
  SHA-256 摘要，"摘要唯一且每用户可并存多条会话"由 0024 的 partial unique 兜底。
- `find_by_token_hash` 按摘要定位（**不含租户谓词**：Cookie 里只有会话原文，租户/用户由会话行导出）。
- `revoke` 只改本行（登出只撤本会话）；来源 Token 的**关联批量撤销**由
  `ChatTokenRepository.revoke` 在同一事务完成，本层不复制那套语义。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.auth import ChatAccessToken, ChatSession
from ..models.control import PlatformUser


@dataclass(frozen=True, slots=True)
class ChatSessionFacts:
    """读取路径一次 JOIN 取齐的事实：会话、来源 Token、用户与 PG 当前时间。"""

    session: ChatSession
    token: ChatAccessToken | None
    user: PlatformUser | None
    db_now: datetime


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

    async def find_facts_by_token_hash(self, token_hash: str) -> ChatSessionFacts | None:
        """按会话摘要一次取齐会话/来源 Token/用户（读取热路径只发一条 SELECT）。

        外连接保证"来源 Token/用户缺失"也能被服务层按既有错误优先级裁决，而不是把
        缺行误判成未知会话；`is_deleted` 谓词放在 JOIN 条件里，软删的关联行等价于缺失。
        """
        row = (
            await self._session.execute(
                select(ChatSession, ChatAccessToken, PlatformUser, func.now())
                .outerjoin(
                    ChatAccessToken,
                    (ChatAccessToken.id == ChatSession.access_token_id)
                    & (ChatAccessToken.is_deleted.is_(False)),
                )
                .outerjoin(
                    PlatformUser,
                    (PlatformUser.id == ChatSession.platform_user_id)
                    & (PlatformUser.tenant_id == ChatSession.tenant_id)
                    & (PlatformUser.is_deleted.is_(False)),
                )
                .where(
                    ChatSession.token_hash == token_hash,
                    ChatSession.is_deleted.is_(False),
                )
            )
        ).first()
        if row is None:
            return None
        session_row, token_row, user_row, db_now = row
        return ChatSessionFacts(
            session=session_row,
            token=token_row,
            user=user_row,
            db_now=db_now,
        )

    async def add(self, row: ChatSession) -> ChatSession:
        self._session.add(row)
        await self._session.flush()
        return row

    async def revoke(self, row: ChatSession, revoked_at: datetime) -> None:
        """撤销本会话并留 `update_time` 痕迹；调用方负责幂等判定（已撤销的行不重复改写）。"""
        row.revoked_at = revoked_at
        row.update_time = revoked_at
        await self._session.flush()
