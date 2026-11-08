"""把产物**发布到目标会话的持久流**（站内入口的交付面；设计 §3.6 附件交付）。

站内入口（WebChat）没有外部推送目标：用户的出站面就是这条会话自己的持久流与历史。
因此交付 = 在**目标会话**里追加一条 canonical `ARTIFACT_CREATED`：

- **发布事实唯一**：载荷就是公开 `WebchatAttachment` DTO（发布这一刻的 size/checksum/
  文件名），实时投影与历史读取都读这一份；产物行仍归属产生它的会话/Run，不复制字节、
  不改归属。
- **幂等**：`source_event_id = uuid5(会话, Run, 产物, 版本)`，由 `uq_canonical_event_source`
  的唯一偏索引兜底 —— 同一次发布的重试拿回原 seq，并发重试只有一个赢家。
- **事务只含 DB 语句**：不读文件、不算哈希、不发网络；提交成功即「已发布」，提交后才唤醒
  追尾订阅者（唤醒失败只记录：追尾有 PG 有界补查兜底，不能把已提交的发布报成失败）。
- **归属**：发布前核验目标会话属于本 Run 的用户与 Agent，且源产物同样属于同一用户、同一
  Agent（同租户）——跨 Agent 的历史产出不得借一次发布泄露到当前会话。
"""

from __future__ import annotations

import logging
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from ...infrastructure.db import SessionFactoryProvider
from ...infrastructure.models.runtime import Artifact, Conversation, RunRecord
from ..event_tail_hub import get_event_tail_hub
from ..public_events import project_attachment
from ..run_events import EventWriter
from .inbound import INBOUND_DOCUMENT, INBOUND_IMAGE, INBOUND_OTHER
from .output_service import AGENT_OUTPUT_ARTIFACT_TYPE
from .transcripts import TRANSCRIPT_ARTIFACT_TYPE

logger = logging.getLogger(__name__)

#: 发布事件的 canonical 身份（公开流事件词表与 `run_service.STREAM_BUSINESS_TYPES` 同源）。
PUBLISH_EVENT_TYPE = "ARTIFACT_CREATED"
PUBLISH_STREAM_TYPE = "artifact.created"

#: 发布幂等键的命名空间（固定值：换个命名空间等于换一套幂等身份）。
PUBLISH_NAMESPACE = uuid.UUID("6f0e4d3a-2c1b-4b8f-9a77-5f1d2c3e4a50")

#: 可以发布给用户的产物类型（与工具侧 `_delivery_refusal` 同一策略：自产产物 + 入站附件）。
PUBLISHABLE_ARTIFACT_TYPES = frozenset(
    {AGENT_OUTPUT_ARTIFACT_TYPE, INBOUND_DOCUMENT, INBOUND_IMAGE, INBOUND_OTHER}
)

PUBLICATION_NOT_FOUND = "ATTACHMENT_NOT_FOUND"
PUBLICATION_TYPE_UNSUPPORTED = "ATTACHMENT_TYPE_UNSUPPORTED"
PUBLICATION_UNAVAILABLE = "ATTACHMENT_WRITE_UNAVAILABLE"


class PublicationError(RuntimeError):
    """发布被拒绝：`code` 与工具层稳定错误码同源，调用方据此给模型稳定原因。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def source_event_id(
    *, conversation_id: uuid.UUID, run_id: uuid.UUID, artifact_id: uuid.UUID, checksum: str
) -> uuid.UUID:
    """一次发布的稳定身份：同一 (会话, Run, 产物, 版本) 的重试必须映射到同一行。"""
    return uuid.uuid5(
        PUBLISH_NAMESPACE, f"publish:{conversation_id}:{run_id}:{artifact_id}:{checksum}"
    )


async def publish_artifact_to_conversation(
    session_factory: SessionFactoryProvider,
    *,
    tenant_id: str,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> int:
    """把一条已存在的产物发布到目标会话；返回发布事件的持久 `seq`。

    事务内只做归属核验与一条事件写入；提交后才唤醒，唤醒失败不影响「已发布」结论。
    """
    async with session_factory()() as session, session.begin():
        run = await _load_run(session, tenant_id=tenant_id, run_id=run_id, conversation_id=conversation_id)
        row = await _load_publishable(session, tenant_id=tenant_id, artifact_id=artifact_id)
        await _assert_same_owner(session, tenant_id=tenant_id, run=run, row=row)
        payload = _payload_of(row)
        seq = await EventWriter(session).append(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            event_type=PUBLISH_EVENT_TYPE,
            stream_type=PUBLISH_STREAM_TYPE,
            payload=payload,
            artifact_id=row.id,
            source_event_id=source_event_id(
                conversation_id=conversation_id,
                run_id=run_id,
                artifact_id=row.id,
                checksum=row.checksum,
            ),
        )
    await _wake(tenant_id=tenant_id, conversation_id=conversation_id)
    return seq


async def _load_run(
    session: AsyncSession,
    *,
    tenant_id: str,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> RunRecord:
    run = await session.scalar(
        sa.select(RunRecord).where(
            RunRecord.id == run_id,
            RunRecord.tenant_id == tenant_id,
            RunRecord.conversation_id == conversation_id,
            RunRecord.is_deleted.is_(False),
        )
    )
    if run is None:
        raise PublicationError(PUBLICATION_NOT_FOUND, "当前运行不属于这个会话")
    return run


async def _load_publishable(
    session: AsyncSession, *, tenant_id: str, artifact_id: uuid.UUID
) -> Artifact:
    row = await session.scalar(
        sa.select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.tenant_id == tenant_id,
            Artifact.is_deleted.is_(False),
        )
    )
    if row is None:
        raise PublicationError(PUBLICATION_NOT_FOUND, "附件不存在或不可访问")
    if row.artifact_type == TRANSCRIPT_ARTIFACT_TYPE or row.artifact_type not in PUBLISHABLE_ARTIFACT_TYPES:
        raise PublicationError(PUBLICATION_TYPE_UNSUPPORTED, "这类产物不能交付给用户")
    if row.conversation_id is None:
        # 站内取件面按会话裁决（后台产出的无会话产物取不到字节）：先显式拒绝，
        # 不发布一张点了会 404 的卡。
        raise PublicationError(PUBLICATION_UNAVAILABLE, "这类产物没有会话归属，网页取件面无法下载")
    return row


async def _assert_same_owner(
    session: AsyncSession,
    *,
    tenant_id: str,
    run: RunRecord,
    row: Artifact,
) -> None:
    """源产物必须属于**同一用户、同一 Agent**：跨 Agent 的历史产出不得借发布越界。"""
    source_user, source_agent = await _source_owner(session, tenant_id=tenant_id, row=row)
    if source_user != run.user_id or source_agent != run.agent_id:
        raise PublicationError(PUBLICATION_TYPE_UNSUPPORTED, "这类产物不能交付给用户")


async def _source_owner(
    session: AsyncSession, *, tenant_id: str, row: Artifact
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    if row.conversation_id is not None:
        conversation = await session.scalar(
            sa.select(Conversation).where(
                Conversation.id == row.conversation_id,
                Conversation.tenant_id == tenant_id,
                Conversation.is_deleted.is_(False),
            )
        )
        if conversation is None:
            return None, None
        return conversation.user_id, conversation.agent_id
    if row.run_id is not None:
        run = await session.scalar(
            sa.select(RunRecord).where(
                RunRecord.id == row.run_id,
                RunRecord.tenant_id == tenant_id,
                RunRecord.is_deleted.is_(False),
            )
        )
        if run is None:
            return None, None
        return run.user_id, run.agent_id
    return None, None


def _payload_of(row: Artifact) -> dict[str, object]:
    """发布载荷 = 公开附件 DTO 的线格式（渠道中立、无存储键、无内部字段）。"""
    attachment = project_attachment(row.id, {row.id: row})
    if attachment is None:
        raise PublicationError(PUBLICATION_TYPE_UNSUPPORTED, "这类产物不能交付给用户")
    return attachment.model_dump(mode="json")


async def _wake(*, tenant_id: str, conversation_id: uuid.UUID) -> None:
    """提交后唤醒追尾订阅者：失败只降级为 PG 有界补查，不影响已提交的发布。"""
    try:
        await get_event_tail_hub().publish(tenant_id=tenant_id, conversation_id=conversation_id)
    except Exception as exc:  # noqa: BLE001 - 唤醒是提示，绝不允许回滚已提交的发布
        logger.warning(
            "artifact_publication_wake_failed conversation_id=%s error=%s",
            conversation_id,
            type(exc).__name__,
        )
