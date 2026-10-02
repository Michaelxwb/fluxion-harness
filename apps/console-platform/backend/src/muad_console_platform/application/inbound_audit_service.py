"""入站事件审计写入（设计 §3.3 / API-10）。

**为什么权威方是 console**：网关不持库（架构测试把它钉在 `sqlalchemy` 之外），入站接收/拒绝
只能经内部端点交过来；runtime 的三张审计表语义是工具/出站/模型调用，没有"入站"这一面。

**为什么不需要脱敏**：入参契约 `InboundAuditRequest` **全部字段都是枚举化的**，没有自由 JSON
字段——`aes_key` 与媒体 URL 在类型上就无处可放。这是"结构保证"而不是"写前过滤"，后者一旦有人
新增一个自由字段就会静默失效。
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from muad_contracts import InboundAuditRequest
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import InboundAudit

#: 幂等键：企微会重投（与 E-07 同源），同一消息的同一结局只留一行。
_IDEMPOTENCY_COLUMNS = ("tenant_id", "channel", "external_message_id", "outcome")
_PARTIAL_UNIQUE_WHERE = sa.text("is_deleted = false")


class InboundAuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, tenant_id: str, payload: InboundAuditRequest) -> uuid.UUID:
        """写一条入站审计；**重复投递返回既有行**而不是再插一条。

        用 `ON CONFLICT DO NOTHING` + 回查，而不是"先查再插"：并发重投时后者会两个都查不到、
        插两条，唯一索引再抛错。幂等的最终保证是那条 **partial unique 索引**，这里只是顺着它走。
        """
        await self._session.execute(
            pg_insert(InboundAudit)
            .values(
                tenant_id=tenant_id,
                channel=payload.channel,
                bot_id=payload.bot_id,
                external_message_id=payload.external_message_id,
                external_user_id=payload.external_user_id,
                outcome=payload.outcome,
                reason_code=payload.reason_code,
                attachment_count=payload.attachment_count,
                accepted_count=payload.accepted_count,
                total_bytes=payload.total_bytes,
                trace_id=payload.trace_id,
            )
            .on_conflict_do_nothing(
                index_elements=list(_IDEMPOTENCY_COLUMNS),
                index_where=_PARTIAL_UNIQUE_WHERE,
            )
        )
        existing = await self._session.scalar(
            sa.select(InboundAudit.id).where(
                InboundAudit.tenant_id == tenant_id,
                InboundAudit.channel == payload.channel,
                InboundAudit.external_message_id == payload.external_message_id,
                InboundAudit.outcome == payload.outcome,
                InboundAudit.is_deleted.is_(False),
            )
        )
        if existing is None:  # 只在并发删改下才可能发生；宁可显式失败也不返回假 id
            raise RuntimeError("inbound audit row missing after upsert")
        return existing
