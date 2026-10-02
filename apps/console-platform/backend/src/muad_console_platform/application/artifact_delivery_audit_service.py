"""交付审计写入（设计 §3.3 / API-03）。

**为什么权威方是 console**：网关不持库（架构测试把它钉在 `sqlalchemy` 之外），交付结果只能经
内部端点交过来；runtime 侧那几张审计表的语义是工具/出站/模型调用，没有"交付给谁"这一面。

**为什么不需要脱敏**：入参契约 `ArtifactDeliveryAuditRequest` **全部字段都是具名且枚举化的**，
没有自由 JSON 字段——交付凭据与取件令牌在类型上就无处可放。这是"结构保证"而不是"写前过滤"，
后者一旦有人新增一个自由字段就会静默失效。

**幂等语义（唯一键的权威定义在设计 §3.3，不在这里）**：键是
`(tenant_id, artifact_id, route_key)`，**`outcome` 不在键里**——它是该行的**当前状态**。
失败重试成功 = **更新同一行**，于是 S-10 的"审计仍只有一行"与 E-06 的"失败 → 重试成功"
两条断言能同时成立。`DELIVERED` 是**终态**：后到的写会落空，重放只读回既有行。
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from muad_contracts import ArtifactDeliveryAuditRequest
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import ArtifactDeliveryAudit

#: 幂等键。**不含 `delivery_key`**：会话内与后台是两条传输路径，但"某产物已交付给某路由"
#: 是同一个事实，不该因为走的路径不同而记两行。
_IDEMPOTENCY_COLUMNS = ("tenant_id", "artifact_id", "route_key")
_PARTIAL_UNIQUE_WHERE = sa.text("is_deleted = false")
#: 终态：交付成功之后这一行不再被任何后续写覆盖
_TERMINAL_OUTCOME = "DELIVERED"


class ArtifactDeliveryAuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, tenant_id: str, payload: ArtifactDeliveryAuditRequest) -> uuid.UUID:
        """写一条交付审计：**同键更新同一行**，已 `DELIVERED` 的行原地不动。

        用 `ON CONFLICT DO UPDATE ... WHERE` + 回查，而不是"先查再写"：并发重试时后者会两个
        都查不到、各写一条，再靠唯一索引抛错。幂等的最终保证是那条 partial unique 索引，
        这里只是顺着它走。
        """
        insert_statement = pg_insert(ArtifactDeliveryAudit).values(
            tenant_id=tenant_id,
            artifact_id=payload.artifact_id,
            channel=payload.channel,
            route_key=payload.route_key,
            delivery_key=payload.delivery_key,
            outcome=payload.outcome,
            reason_code=payload.reason_code,
            trace_id=payload.trace_id,
        )
        await self._session.execute(
            insert_statement.on_conflict_do_update(
                index_elements=list(_IDEMPOTENCY_COLUMNS),
                index_where=_PARTIAL_UNIQUE_WHERE,
                set_={
                    "outcome": insert_statement.excluded.outcome,
                    "reason_code": insert_statement.excluded.reason_code,
                    "delivery_key": insert_statement.excluded.delivery_key,
                    "trace_id": insert_statement.excluded.trace_id,
                    "update_time": sa.func.now(),
                },
                # 已交付的行是终态：这里的 where 针对**既有行**，条件不成立就不更新
                where=ArtifactDeliveryAudit.outcome != _TERMINAL_OUTCOME,
            )
        )
        existing = await self._session.scalar(
            sa.select(ArtifactDeliveryAudit.id).where(
                ArtifactDeliveryAudit.tenant_id == tenant_id,
                ArtifactDeliveryAudit.artifact_id == payload.artifact_id,
                ArtifactDeliveryAudit.route_key == payload.route_key,
                ArtifactDeliveryAudit.is_deleted.is_(False),
            )
        )
        if existing is None:  # 只在并发删改下才可能发生；宁可显式失败也不返回假 id
            raise RuntimeError("artifact delivery audit row missing after upsert")
        return existing
