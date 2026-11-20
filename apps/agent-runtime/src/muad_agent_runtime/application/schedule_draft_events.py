"""`schedule.draft.ready` 的 canonical 写入：Draft 的确认卡片必须是一条**持久事件**。

三条硬边界（design §3.2.8 / §3.5.4 / §3.5.10）：

- **不是 Run 的确认通道**：`confirmation.required`/interrupt/resume 确认的是「这次 Run 是否
  继续」，恢复后由同一个 Run 接着跑；Draft 确认的是「这条配置是否生效」，走 surface 的确认
  端点（API-31 → API-27），与 Run 生命周期无关。两者共用词表会让客户端调错端点。
- **持久且可重建**：卡片只从 canonical 行投影（历史与 SSE 读同一份载荷），不是当次 SSE 里
  临时拼出来的一次性事实；刷新/重连都按 `after_seq` 重放这一行。
- **幂等**：`source_event_id` 由 (Run, Draft, hash) 派生 —— 同一个工具调用的重试拿回原 seq，
  不会在同一会话里长出第二张卡片。

事务里只有一条事件写入；提交后才唤醒追尾订阅者（唤醒失败只降级为有界补查）。
"""

from __future__ import annotations

import uuid

from muad_contracts.webchat import WebchatScheduleDraftReady
from muad_logging import get_logger

from ..infrastructure.db import SessionFactoryProvider
from .event_tail_hub import get_event_tail_hub
from .run_events import EventWriter

logger = get_logger(__name__)

#: canonical 身份：`event_type` 是业务审计名，`stream_type` 是公开流词表里的判别值。
SCHEDULE_DRAFT_READY_EVENT = "SCHEDULE_DRAFT_READY"
SCHEDULE_DRAFT_READY_STREAM_TYPE = "schedule.draft.ready"

#: `source_event_id` 的命名空间（固定值：换命名空间等于换一套幂等身份）。
DRAFT_READY_NAMESPACE = uuid.UUID("0c8f5d2e-76b4-4a19-9e63-1f9a7c2d5b08")


def draft_ready_source_event_id(
    *, run_id: uuid.UUID, draft_id: uuid.UUID, draft_hash: str
) -> uuid.UUID:
    """同一 Run 内同一 Draft（同 hash）的重试必须映射到同一行。"""
    return uuid.uuid5(
        DRAFT_READY_NAMESPACE, f"schedule-draft:{run_id}:{draft_id}:{draft_hash}"
    )


async def publish_schedule_draft_ready(
    session_factory: SessionFactoryProvider,
    *,
    tenant_id: str,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    card: WebchatScheduleDraftReady,
) -> int:
    """写入 Draft 确认卡片事件并返回持久 `seq`；同一 (Run, Draft, hash) 重放原行。"""
    async with session_factory()() as session, session.begin():
        seq = await EventWriter(session).append(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            event_type=SCHEDULE_DRAFT_READY_EVENT,
            stream_type=SCHEDULE_DRAFT_READY_STREAM_TYPE,
            payload=card.model_dump(mode="json"),
            source_event_id=draft_ready_source_event_id(
                run_id=run_id, draft_id=card.draft_id, draft_hash=card.draft_hash
            ),
        )
    await _wake(tenant_id=tenant_id, conversation_id=conversation_id)
    return seq


async def _wake(*, tenant_id: str, conversation_id: uuid.UUID) -> None:
    """提交后唤醒追尾订阅者：失败只降级为 PG 有界补查，不影响已提交的事件。"""
    try:
        await get_event_tail_hub().publish(tenant_id=tenant_id, conversation_id=conversation_id)
    except Exception as exc:  # noqa: BLE001 - 唤醒是提示，绝不允许回滚已提交的事实
        logger.warning(
            "schedule_draft_ready_wake_failed conversation_id=%s error=%s",
            conversation_id,
            type(exc).__name__,
        )
