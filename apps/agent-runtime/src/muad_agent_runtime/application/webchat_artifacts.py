"""Webchat 附件下载授权解析（design §3.4 API-10、§3.6 附件与重试生命周期；TASK-055）。

职责与边界：

- **归属在 Owner**：`artifact_id` 只是引用，能不能取件由**目标会话**裁决 —— 解析请求携带
  用户正在看的会话（`conversation_id`）与卡片上的版本（`checksum`）：
  - 产物就属于这个会话（用户上传、本会话产出）⇒ 按本会话授权；
  - 产物来自别的会话（例如企微生成的产出被发到网页会话）⇒ 该会话里必须存在**发布事实**
    （`ARTIFACT_CREATED` 行，载荷里冻结着发布那一刻的 checksum），且源产物与本会话同用户、
    同 Agent。
  入口/Agent 从**目标会话**行导出（调用方不自报），再交给 Gateway 复核当前入口/Agent 授权。
- **版本冻结**：按 `checksum` 定位发布时的字节 —— 当前版本命中就发当前，否则在版本历史里
  找回那一版；都找不到按"拿不到"处理，**绝不**把改过的内容当成发布的那一份。
- **不泄露存在性**：不存在 / 跨租户 / 他主体会话 / 已软删 / 不可归属一律 `COMMON_NOT_FOUND`
  —— 响应不该让调用方分辨出"这个 id 存在但你没权限"。
- **内部留档不是公开附件**：`TRANSCRIPT` 类产物只作审计留档（公开投影同口径），没有下载语义。
- **只解析、不取字节**：返回**渠道中立**的 `AttachmentRef`（`attachment_ref` 单点映射，与
  Console 取件、后台投递同一份口径）；字节由 Gateway 在 DB 事务释放后从共享存储流式读出。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import AttachmentRef
from muad_contracts.webchat import WebchatConversationScope
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.runtime import Artifact, CanonicalEvent, Conversation, RunRecord
from .attachments.publication import PUBLISH_EVENT_TYPE
from .attachments.reference import attachment_ref
from .attachments.transcripts import TRANSCRIPT_ARTIFACT_TYPE


@dataclass(frozen=True, slots=True)
class WebchatArtifactDownload:
    """下载解析结论：会话归属 + 渠道中立的引用（存储键只在本受信面传递，不进公开响应）。"""

    scope: WebchatConversationScope
    reference: AttachmentRef


async def resolve_webchat_artifact(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID,
    conversation_id: uuid.UUID,
    checksum: str,
) -> WebchatArtifactDownload:
    """(会话, 产物, 版本) → (会话归属, 渠道中立引用)；任何一步不成立一律 `COMMON_NOT_FOUND`。"""
    conversation, entry_id = await _owned_conversation(
        session, tenant_id=tenant_id, user_id=user_id, conversation_id=conversation_id
    )
    row = await _owned_artifact(session, tenant_id=tenant_id, artifact_id=artifact_id)
    if row.conversation_id != conversation.id:
        await _assert_published(
            session, tenant_id=tenant_id, conversation=conversation, artifact_id=artifact_id,
            checksum=checksum,
        )
        user, agent = await _source_owner(session, tenant_id=tenant_id, row=row)
        if user != conversation.user_id or agent != conversation.agent_id:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
    return WebchatArtifactDownload(
        scope=WebchatConversationScope(
            conversation_id=conversation.id, entry_id=entry_id, agent_id=conversation.agent_id
        ),
        reference=_reference_at(row, checksum),
    )


async def _owned_artifact(
    session: AsyncSession, *, tenant_id: str, artifact_id: uuid.UUID
) -> Artifact:
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
    return row


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


async def _assert_published(
    session: AsyncSession,
    *,
    tenant_id: str,
    conversation: Conversation,
    artifact_id: uuid.UUID,
    checksum: str,
) -> None:
    """跨会话取件的前提：目标会话里有这个产物**这个版本**的发布事实（卡片的来源）。"""
    published = await session.scalar(
        sa.select(sa.literal(1))
        .select_from(CanonicalEvent)
        .where(
            CanonicalEvent.tenant_id == tenant_id,
            CanonicalEvent.conversation_id == conversation.id,
            CanonicalEvent.event_type == PUBLISH_EVENT_TYPE,
            CanonicalEvent.artifact_id == artifact_id,
            CanonicalEvent.is_deleted.is_(False),
            CanonicalEvent.payload_json["checksum"].astext == checksum,
        )
        .limit(1)
    )
    if published is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)


async def _source_owner(
    session: AsyncSession, *, tenant_id: str, row: Artifact
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """源产物的 (user, agent)：来自它的会话，或（后台产出）来自它的 Run。"""
    if row.conversation_id is not None:
        source = await session.scalar(
            sa.select(Conversation).where(
                Conversation.id == row.conversation_id,
                Conversation.tenant_id == tenant_id,
                Conversation.is_deleted.is_(False),
            )
        )
        return (source.user_id, source.agent_id) if source is not None else (None, None)
    if row.run_id is not None:
        source_run = await session.scalar(
            sa.select(RunRecord).where(
                RunRecord.id == row.run_id,
                RunRecord.tenant_id == tenant_id,
                RunRecord.is_deleted.is_(False),
            )
        )
        return (source_run.user_id, source_run.agent_id) if source_run is not None else (None, None)
    return None, None


def _reference_at(row: Artifact, checksum: str) -> AttachmentRef:
    """按 checksum 取发布那一刻的字节：当前版本命中就发当前，否则回到版本历史。"""
    if row.checksum == checksum:
        return attachment_ref(row)
    for entry in _versions(row):
        if entry.get("checksum") == checksum:
            size = entry.get("size")
            key = entry.get("key")
            if isinstance(key, str) and key and isinstance(size, int) and size > 0:
                return attachment_ref(row).model_copy(
                    update={"storage_key": key, "size": size, "checksum": checksum}
                )
    raise AppError(ErrorCode.COMMON_NOT_FOUND)


def _versions(row: Artifact) -> list[dict[str, Any]]:
    """版本历史（append 时按版本落 `{key, checksum, size}`；一次性规范化后没有第二种形状）。"""
    raw = (row.metadata_json or {}).get("versions")
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]
