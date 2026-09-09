"""ChatSessionHead 持久化（ADR-A017 §5）：PG 单库实现。

并发语义（D4）：只用 revision CAS，不用行锁 + revision 双机制；
CAS 失败抛 `VersionConflictError`，由服务层转译为命令错误码。
"""

from __future__ import annotations

from sqlalchemy import insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.registry.channel_store import ChatSessionHead
from fluxion.registry.schema import chat_session_heads
from fluxion.registry.store import VersionConflictError


def _head_from_row(row: RowMapping) -> ChatSessionHead:
    return ChatSessionHead(
        tenant_id=str(row["tenant_id"]),
        channel_type=str(row["channel_type"]),
        external_conversation_id=str(row["external_conversation_id"]),
        platform_user_id=str(row["platform_user_id"]),
        agent_id=str(row["agent_id"]),
        active_session_id=str(row["active_session_id"]),
        revision=int(row["revision"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def get_session_head(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    channel_type: str,
    external_conversation_id: str,
    platform_user_id: str,
    agent_id: str,
) -> ChatSessionHead | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                select(chat_session_heads).where(
                    chat_session_heads.c.tenant_id == tenant_id,
                    chat_session_heads.c.channel_type == channel_type,
                    chat_session_heads.c.external_conversation_id == external_conversation_id,
                    chat_session_heads.c.platform_user_id == platform_user_id,
                    chat_session_heads.c.agent_id == agent_id,
                )
            )
        ).mappings().first()
    if row is None:
        return None
    return _head_from_row(row)


async def create_session_head(engine: AsyncEngine, head: ChatSessionHead) -> ChatSessionHead:
    async with engine.begin() as connection:
        await connection.execute(insert(chat_session_heads).values(**_head_values(head)))
    return head


async def rotate_session_head(
    engine: AsyncEngine, head: ChatSessionHead, *, expected_revision: int
) -> ChatSessionHead:
    """CAS 轮换 active 指针：revision + 1。版本不符即冲突（不重试）。"""
    async with engine.begin() as connection:
        result = await connection.execute(
            update(chat_session_heads)
            .where(
                chat_session_heads.c.tenant_id == head.tenant_id,
                chat_session_heads.c.channel_type == head.channel_type,
                chat_session_heads.c.external_conversation_id == head.external_conversation_id,
                chat_session_heads.c.platform_user_id == head.platform_user_id,
                chat_session_heads.c.agent_id == head.agent_id,
                chat_session_heads.c.revision == expected_revision,
            )
            .values(
                active_session_id=head.active_session_id,
                revision=expected_revision + 1,
                updated_at=head.updated_at,
            )
        )
        if result.rowcount != 1:
            raise VersionConflictError("chat session head 已被并发修改")
    return head


def _head_values(head: ChatSessionHead) -> dict[str, object]:
    return {
        "tenant_id": head.tenant_id,
        "channel_type": head.channel_type,
        "external_conversation_id": head.external_conversation_id,
        "platform_user_id": head.platform_user_id,
        "agent_id": head.agent_id,
        "active_session_id": head.active_session_id,
        "revision": head.revision,
        "created_at": head.created_at,
        "updated_at": head.updated_at,
    }
