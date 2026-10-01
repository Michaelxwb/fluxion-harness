"""受控长期 Memory：PREFERENCE/WORK_STYLE/EXPLICIT 的版本化读写（runtime.user_memory）。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import get_session_factory
from ..infrastructure.models.runtime import UserMemory

WRITE_POLICY_CONTROLLED = "CONTROLLED"

ALLOWED_CATEGORIES = frozenset({"PREFERENCE", "WORK_STYLE", "EXPLICIT"})

# 记忆来源分级：只有用户明确要求的记忆才自动注入（`AGENT_INFERRED` 仅经 `recall` 取回）。
SOURCE_USER_EXPLICIT = "USER_EXPLICIT"
SOURCE_AGENT_INFERRED = "AGENT_INFERRED"


class MemoryService:
    """跨进程边界使用；每次操作持有独立短事务。

    `source_type` **不做白名单校验**：既有行可能带历史取值（如 `EXPLICIT`），
    它们在注入分级中按"非 `USER_EXPLICIT`"处理（即不自动注入），
    而不是让历史数据变成写入错误。入参枚举校验属工具层（`remember`）。
    """

    async def upsert(
        self,
        *,
        tenant_id: str,
        user_id: uuid.UUID,
        category: str,
        memory_key: str,
        content_json: dict[str, Any],
        source_type: str,
        source_ref: str | None = None,
    ) -> dict[str, Any]:
        """同 `(tenant_id, user_id, memory_key)` 覆盖更新：`version+1`、`enabled` 复位、`update_time` 刷新。

        用 `INSERT ... ON CONFLICT DO UPDATE` 单语句完成，而不是"先 select 再 insert/update"：
        后者在并发下会丢版本增量（多个写者都读到同一 version 再各自 +1，最终版本远小于调用次数，
        实测 5 并发只到 2），撞唯一约束时还会直接抛错。

        **`update_time` 取数据库时钟（`now()`），插入与更新同一口径**：它是注入与检索的排序键
        （`ORDER BY update_time DESC`），而 Runtime 多 Pod 无状态、同一用户可被任意 Pod 写入 ——
        用各 Pod 的进程时钟排序跨写者不可比。两个时钟混用还会让"更新必然晚于插入"不成立
        （插入走 `server_default now()`、更新走进程时钟，实测相差约 1ms 且方向不定）。

        **冲突目标必须复述 partial 索引的谓词** `WHERE is_deleted = false`：`user_memory` 上的唯一索引
        是 partial unique，谓词不匹配时 PostgreSQL 推断不出该索引，会报
        "no unique or exclusion constraint matching the ON CONFLICT specification"。
        软删除的旧行因此不参与冲突 —— 同 key 重新写入会新起一行，旧行保留供审计。
        """
        if category not in ALLOWED_CATEGORIES:
            raise ValueError(f"memory category not allowed: {category}")
        statement = (
            pg_insert(UserMemory)
            .values(
                tenant_id=tenant_id,
                user_id=user_id,
                memory_key=memory_key,
                category=category,
                content_json=content_json,
                source_type=source_type,
                source_ref=source_ref,
                write_policy=WRITE_POLICY_CONTROLLED,
                version=1,
                enabled=True,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "user_id", "memory_key"],
                index_where=text("is_deleted = false"),
                set_={
                    "category": category,
                    "content_json": content_json,
                    "source_type": source_type,
                    "source_ref": source_ref,
                    "version": UserMemory.__table__.c.version + 1,
                    # 覆盖更新必须把 `enabled` 置回 true：否则"重新记住"对一条被禁用的记忆不生效，
                    # 而工具仍回执成功（用户被告知记住了、实际永不生效）。
                    "enabled": True,
                    "update_time": func.now(),
                },
            )
            .returning(UserMemory)
        )
        async with get_session_factory()() as session:
            row = (await session.execute(statement)).scalars().one()
            await session.commit()
            return self._snapshot(row)

    async def list_entries(self, tenant_id: str, user_id: uuid.UUID) -> list[dict[str, Any]]:
        async with get_session_factory()() as session:
            rows = (
                await session.execute(
                    select(UserMemory)
                    .where(
                        UserMemory.tenant_id == tenant_id,
                        UserMemory.user_id == user_id,
                        UserMemory.enabled.is_(True),
                        UserMemory.is_deleted.is_(False),
                    )
                    .order_by(UserMemory.memory_key)
                )
            ).scalars().all()
            return [self._snapshot(row) for row in rows]

    async def list_for_injection(
        self, tenant_id: str, user_id: uuid.UUID, *, limit: int
    ) -> list[dict[str, Any]]:
        """自动注入用：**只取 `USER_EXPLICIT`**，按 `update_time DESC` 取最近 `limit` 条。

        分级过滤放在 SQL 层而不是取回后在应用层筛：注入是每轮对话的必经查询，
        `AGENT_INFERRED` 与软删除行不该被读出来（命中 `ix_user_memory_user_enabled_update_time`）。
        条数/字节上限不在这里施加 —— 调用方（ContextBuilder）按预算逐条累加。
        """
        async with get_session_factory()() as session:
            return await self.list_for_injection_with_session(
                session, tenant_id, user_id, limit=limit
            )

    @staticmethod
    async def list_for_injection_with_session(
        session: AsyncSession,
        tenant_id: str,
        user_id: uuid.UUID,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        """在调用方已持有的 session 上执行注入查询。

        ContextBuilder 取历史时已经开着一个会话；再在里层开第二个会话会在**持有连接的同时**
        申请新连接，池紧张时直接把请求拖死。同款先例见
        `ArtifactResultWriter.persist_tool_result_with_session`。
        """
        rows = (
            await session.execute(
                select(UserMemory)
                .where(
                    UserMemory.tenant_id == tenant_id,
                    UserMemory.user_id == user_id,
                    UserMemory.source_type == SOURCE_USER_EXPLICIT,
                    UserMemory.enabled.is_(True),
                    UserMemory.is_deleted.is_(False),
                )
                .order_by(UserMemory.update_time.desc())
                .limit(limit)
            )
        ).scalars().all()
        return [MemoryService._snapshot(row) for row in rows]

    async def search(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        *,
        prefix: str | None = None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """模型按需检索用（`recall`）：前缀过滤 + 最近优先，**两类来源都可取回**。

        与注入的区别是这里没有 `source_type` 过滤 —— `AGENT_INFERRED` 正是靠这条路进入上下文。
        """
        filters = [
            UserMemory.tenant_id == tenant_id,
            UserMemory.user_id == user_id,
            UserMemory.enabled.is_(True),
            UserMemory.is_deleted.is_(False),
        ]
        if prefix:
            filters.append(UserMemory.memory_key.startswith(prefix, autoescape=True))
        async with get_session_factory()() as session:
            rows = (
                await session.execute(
                    select(UserMemory)
                    .where(*filters)
                    .order_by(UserMemory.update_time.desc())
                    .limit(limit)
                )
            ).scalars().all()
            return [self._snapshot(row) for row in rows]

    async def disable(self, tenant_id: str, user_id: uuid.UUID, memory_key: str) -> None:
        """软删除（is_deleted=true）：历史可审计，运行时不再读取。"""
        async with get_session_factory()() as session:
            row = (
                await session.execute(
                    select(UserMemory).where(
                        UserMemory.tenant_id == tenant_id,
                        UserMemory.user_id == user_id,
                        UserMemory.memory_key == memory_key,
                        UserMemory.is_deleted.is_(False),
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                row.is_deleted = True
                await session.commit()

    @staticmethod
    def _snapshot(row: UserMemory) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "user_id": str(row.user_id),
            "memory_key": row.memory_key,
            "category": row.category,
            "content_json": row.content_json,
            "source_type": row.source_type,
            "source_ref": row.source_ref,
            "version": row.version,
            "enabled": row.enabled,
            # 注入与检索都按它排序，调用方需要它来做上限累加与断言
            "update_time": row.update_time,
        }
