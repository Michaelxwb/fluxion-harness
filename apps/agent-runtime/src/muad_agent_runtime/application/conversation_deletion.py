"""会话删除：Owner 侧软删 + 活跃 Run 取消 + 通知收口（设计 §3.3/§3.4/§3.5）。

职责与边界：

- **软删是唯一事实**：置 `is_deleted` 即可让列表、详情、消息、续聊、产物下载与事件追尾一致
  不可见（这些读取路径本来就带 `is_deleted = false` 谓词），不物理删除任何行。
- **同请求取消，不等终态**：删除接口只把活跃 Run 交给既有取消语义处理，不轮询 Run 终态——
  收尾中的 Run 仍会写终态事件与通知，通知由写入侧的会话可见性判定兜住。
- **幂等**：核属查询**包含已软删行**；同一会话重复删除直接返回成功，不再取消任何 Run。
- **事务顺序**：核属与活跃 Run 查询都是只读；`RunService.cancel_run` 内部会提交，因此写之前
  不能 stage 任何变更，软删在取消之后单独提交。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Protocol

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime import (
    ChannelConversation,
    ChannelNotification,
    Conversation,
    RunRecord,
)
from .run_service import ACTIVE_RUN_STATUSES

logger = logging.getLogger(__name__)


class RunCancelPort(Protocol):
    """删除编排要的取消能力（`RunService` 是唯一生产实现；测试替身只需同一动词面）。"""

    async def cancel_run(self, run_id: uuid.UUID, tenant_id: str) -> Any: ...


class ConversationDeletionService:
    """用户主动删除自己的网页会话：先取消活跃 Run，再软删会话与它的通知。"""

    def __init__(self, session: AsyncSession, runs: RunCancelPort) -> None:
        self._session = session
        self._runs = runs

    async def delete(
        self, *, tenant_id: str, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> dict[str, Any]:
        """软删会话；返回对外响应形状（`deleted` 表示当前已达成删除）。"""
        conversation = await self._load_for_delete(tenant_id, user_id, conversation_id)
        if conversation.is_deleted:
            # 幂等分支：上次已删成功，不再取消任何 Run（可能已属于别的会话）。
            return _receipt(conversation.id)
        cancelled_run = await self._active_run_id(tenant_id, conversation.id)
        if cancelled_run is not None:
            await self._cancel(tenant_id, cancelled_run)
        await self._soft_delete(conversation)
        logger.info(
            "webchat_conversation_deleted conversation_id=%s cancelled_run=%s",
            conversation.id,
            cancelled_run is not None,
        )
        return _receipt(conversation.id)

    async def _load_for_delete(
        self, tenant_id: str, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> Conversation:
        """核属：tenant + user + id，**不过滤** `is_deleted`（幂等分支要用 tombstone）。"""
        conversation = await self._session.scalar(
            sa.select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == tenant_id,
                Conversation.user_id == user_id,
            )
        )
        if conversation is None or conversation.channel_account_id is None:
            # 不存在/他主体/无入口归属一律同形，不泄露存在性。
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return conversation

    async def _active_run_id(
        self, tenant_id: str, conversation_id: uuid.UUID
    ) -> uuid.UUID | None:
        """该会话的活跃 Run（`uq_run_record_active_conversation` 保证至多一行）。"""
        return await self._session.scalar(
            sa.select(RunRecord.id)
            .where(
                RunRecord.conversation_id == conversation_id,
                RunRecord.tenant_id == tenant_id,
                RunRecord.is_deleted.is_(False),
                RunRecord.status.in_(ACTIVE_RUN_STATUSES),
            )
            .limit(1)
        )

    async def _cancel(self, tenant_id: str, run_id: uuid.UUID) -> None:
        """复用既有取消语义；CAS 落败（Run 恰好终态）视为已无活跃 Run，继续删除。"""
        try:
            await self._runs.cancel_run(run_id, tenant_id)
        except AppError as exc:
            if exc.code != ErrorCode.RUN_BUSY:
                raise
            logger.info("webchat_conversation_delete_cancel_raced run_id=%s", run_id)

    async def _soft_delete(self, conversation: Conversation) -> None:
        """会话行、指向它的通道指针与该会话的通知在同一事务里软删。"""
        now = await database_now(self._session)
        scope = {"tenant_id": conversation.tenant_id}
        await self._session.execute(
            sa.update(Conversation)
            .where(Conversation.id == conversation.id, Conversation.tenant_id == scope["tenant_id"])
            .values(is_deleted=True, update_time=now)
        )
        await self._session.execute(
            sa.update(ChannelConversation)
            .where(
                ChannelConversation.conversation_id == conversation.id,
                ChannelConversation.tenant_id == scope["tenant_id"],
                ChannelConversation.is_deleted.is_(False),
            )
            .values(is_deleted=True, update_time=now)
        )
        await self._session.execute(
            sa.update(ChannelNotification)
            .where(
                ChannelNotification.tenant_id == scope["tenant_id"],
                ChannelNotification.user_id == conversation.user_id,
                ChannelNotification.conversation_id == conversation.id,
                ChannelNotification.is_deleted.is_(False),
            )
            .values(is_deleted=True, update_time=now)
        )
        await self._session.commit()


def _receipt(conversation_id: uuid.UUID) -> dict[str, Any]:
    return {"conversation_id": str(conversation_id), "deleted": True}
