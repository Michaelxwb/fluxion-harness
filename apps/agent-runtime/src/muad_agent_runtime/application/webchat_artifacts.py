"""Webchat 附件下载授权解析（design §3.4 API-10、§3.6 附件与重试生命周期；TASK-055）。

职责与边界：

- **归属在 Owner**：`artifact_id` 只是引用，能不能取件由**本主体名下的会话**裁决 ——
  artifact → conversation 的 `(tenant, user)` 核属是唯一的归属事实；入口/Agent 从会话行
  导出（调用方不自报），再交给 Gateway 复核**当前**入口/Agent 授权（授权集合来自 Console，
  只有认证面知道）。
- **不泄露存在性**：不存在 / 跨租户 / 他主体会话 / 已软删 / 不可归属一律 `COMMON_NOT_FOUND`——
  响应不该让调用方分辨出"这个 id 存在但你没权限"。
- **内部留档不是公开附件**：`TRANSCRIPT` 类产物只作审计留档（公开投影同口径），没有下载语义。
- **只解析、不取字节**：返回**渠道中立**的 `AttachmentRef`（`attachment_ref` 单点映射，与
  Console 取件、后台投递同一份口径）；字节由 Gateway 在 DB 事务释放后从共享存储流式读出。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import AttachmentRef
from muad_contracts.webchat import WebchatConversationScope
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.runtime import Artifact, Conversation
from .attachments.reference import attachment_ref
from .attachments.transcripts import TRANSCRIPT_ARTIFACT_TYPE


@dataclass(frozen=True, slots=True)
class WebchatArtifactDownload:
    """下载解析结论：会话归属 + 渠道中立的引用（存储键只在本受信面传递，不进公开响应）。"""

    scope: WebchatConversationScope
    reference: AttachmentRef


async def resolve_webchat_artifact(
    session: AsyncSession, *, tenant_id: str, user_id: uuid.UUID, artifact_id: uuid.UUID
) -> WebchatArtifactDownload:
    """`artifact_id` → (会话归属, 渠道中立引用)；任何一步不成立一律 `COMMON_NOT_FOUND`。"""
    row, conversation_id = await _owned_artifact(
        session, tenant_id=tenant_id, artifact_id=artifact_id
    )
    conversation, entry_id = await _owned_conversation(
        session, tenant_id=tenant_id, user_id=user_id, conversation_id=conversation_id
    )
    return WebchatArtifactDownload(
        scope=WebchatConversationScope(
            conversation_id=conversation.id, entry_id=entry_id, agent_id=conversation.agent_id
        ),
        reference=attachment_ref(row),
    )


async def _owned_artifact(
    session: AsyncSession, *, tenant_id: str, artifact_id: uuid.UUID
) -> tuple[Artifact, uuid.UUID]:
    """取本租户的**活跃**产物行：跨租户/不存在/软删/内部留档/无会话归属都视同不存在。"""
    row = await session.scalar(
        sa.select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.tenant_id == tenant_id,
            Artifact.is_deleted.is_(False),
        )
    )
    if row is None or row.conversation_id is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    if row.artifact_type == TRANSCRIPT_ARTIFACT_TYPE:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    return row, row.conversation_id


async def _owned_conversation(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> tuple[Conversation, uuid.UUID]:
    """会话主体核属：他主体的会话视同不存在（不泄露"这个产物存在但属于别人"）。

    入口缺失（无法归属到任何公开入口）同样是"拿不到"：下载面不能从不可归属的历史里取字节。
    """
    conversation = await session.scalar(
        sa.select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == tenant_id,
            Conversation.user_id == user_id,
            Conversation.is_deleted.is_(False),
        )
    )
    if conversation is None or conversation.channel_account_id is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    return conversation, conversation.channel_account_id
