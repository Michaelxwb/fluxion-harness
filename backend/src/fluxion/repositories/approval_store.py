"""PostgresApprovalStore：ApprovalStore 的 PG 持久化实现（Phase 6 TASK-006，P0-5）。

production profile 的「显式 production adapter」——与 ``InMemoryApprovalStore``
同形（create/get/decide/consume），审批决策跨进程持久化。

- decide 原子性：单条 UPDATE ... WHERE status='pending'（行级 CAS，多实例安全）；
- consume 原子性（A9）：UPDATE ... WHERE consumed_at IS NULL——已消费审批单
  不可重放，多实例由 DB 级 CAS 保证（InMemory 版依赖进程内 publication lock）；
- 全方法 deadline（规则 18）；tenant scope 强制（规则 16）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import datetime
from typing import Any, TypeVar

from sqlalchemy import insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.registry.schema import approval_records
from fluxion.services.approval_app import ApprovalRecord, ApprovalStatus

_T = TypeVar("_T")
_TIMEOUT_SECONDS = 10.0


class ApprovalStorePersistenceError(RuntimeError):
    """ApprovalStore 持久化失败（明确失败，不静默）。"""

    code = "approval_store_error"


class PostgresApprovalStore:
    """审批决策落库实现（engine 注入：SQLite 契约 / PostgreSQL 生产）。"""

    def __init__(
        self, *, engine: AsyncEngine, timeout_seconds: float = _TIMEOUT_SECONDS
    ) -> None:
        self._engine = engine
        self._timeout_seconds = timeout_seconds

    async def initialize(self) -> None:
        """幂等建表（approval_records）。"""
        async with self._engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: approval_records.create(sync_conn, checkfirst=True)
            )

    async def create(self, record: ApprovalRecord) -> ApprovalRecord:
        async def _create() -> None:
            async with self._engine.begin() as conn:
                existing: Any = await conn.execute(
                    select(approval_records.c.approval_id).where(
                        approval_records.c.tenant_id == record.tenant_id,
                        approval_records.c.approval_id == record.approval_id,
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    raise ValueError(
                        f"approval {record.approval_id} already exists"
                    )
                await conn.execute(
                    insert(approval_records).values(**_to_row(record))
                )

        await self._with_deadline(_create(), f"create {record.approval_id}")
        return record

    async def get(self, approval_id: str, *, tenant_id: str) -> ApprovalRecord | None:
        async def _get() -> Any:
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    select(approval_records).where(
                        approval_records.c.tenant_id == tenant_id,
                        approval_records.c.approval_id == approval_id,
                    )
                )
                return result.mappings().first()

        row = await self._with_deadline(_get(), f"get {approval_id}")
        return _from_row(row) if row is not None else None

    async def decide(
        self,
        approval_id: str,
        *,
        tenant_id: str,
        approver_actor_id: str,
        approved: bool,
        reason: str | None,
        decided_at: datetime,
    ) -> ApprovalRecord:
        status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED

        async def _decide() -> Any:
            async with self._engine.begin() as conn:
                result = await conn.execute(
                    update(approval_records)
                    .where(
                        approval_records.c.tenant_id == tenant_id,
                        approval_records.c.approval_id == approval_id,
                        approval_records.c.status == ApprovalStatus.PENDING.value,
                    )
                    .values(
                        status=status.value,
                        approver_actor_id=approver_actor_id,
                        reason=reason,
                        decided_at=decided_at,
                    )
                )
                if result.rowcount == 0:
                    # 不存在或已决策——区分错误语义（与 InMemory 一致）
                    existing: Any = await conn.execute(
                        select(approval_records.c.status).where(
                            approval_records.c.tenant_id == tenant_id,
                            approval_records.c.approval_id == approval_id,
                        )
                    )
                    if existing.scalar_one_or_none() is None:
                        raise KeyError(approval_id)
                    raise ValueError(f"approval {approval_id} already decided")

        await self._with_deadline(_decide(), f"decide {approval_id}")
        decided = await self.get(approval_id, tenant_id=tenant_id)
        assert decided is not None
        return decided

    async def consume(
        self,
        approval_id: str,
        *,
        tenant_id: str,
        consumed_at: datetime,
    ) -> ApprovalRecord:
        async def _consume() -> Any:
            async with self._engine.begin() as conn:
                # A9：DB 级 CAS——仅 consumed_at 仍为 NULL 时置位
                result = await conn.execute(
                    update(approval_records)
                    .where(
                        approval_records.c.tenant_id == tenant_id,
                        approval_records.c.approval_id == approval_id,
                        approval_records.c.consumed_at.is_(None),
                    )
                    .values(consumed_at=consumed_at)
                )
                if result.rowcount == 0:
                    existing: Any = await conn.execute(
                        select(approval_records.c.consumed_at).where(
                            approval_records.c.tenant_id == tenant_id,
                            approval_records.c.approval_id == approval_id,
                        )
                    )
                    if existing.scalar_one_or_none() is None:
                        raise KeyError(approval_id)
                    raise ValueError(f"approval {approval_id} already consumed")

        await self._with_deadline(_consume(), f"consume {approval_id}")
        consumed = await self.get(approval_id, tenant_id=tenant_id)
        assert consumed is not None
        return consumed

    async def _with_deadline(
        self, coro: Coroutine[Any, Any, _T], label: str
    ) -> _T:
        try:
            return await asyncio.wait_for(coro, timeout=self._timeout_seconds)
        except TimeoutError as error:
            raise ApprovalStorePersistenceError(
                f"{label} 超时（>{self._timeout_seconds}s）"
            ) from error
        except SQLAlchemyError as error:
            raise ApprovalStorePersistenceError(f"{label} 失败: {error}") from error


# ---------------------------------------------------------------------------
# ApprovalRecord ⇄ 行 序列化
#


def _to_row(record: ApprovalRecord) -> dict[str, Any]:
    kind_value = (
        record.kind.value if hasattr(record.kind, "value") else str(record.kind)
    )
    return {
        "tenant_id": record.tenant_id,
        "approval_id": record.approval_id,
        "kind": kind_value,
        "resource_id": record.resource_id,
        "target_version": record.target_version,
        "operation": record.operation,
        "requester_actor_id": record.requester_actor_id,
        "status": record.status.value,
        "approver_actor_id": record.approver_actor_id,
        "reason": record.reason,
        "expires_at": record.expires_at,
        "created_at": record.created_at,
        "decided_at": record.decided_at,
        "consumed_at": record.consumed_at,
    }


def _from_row(row: Any) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=str(row["approval_id"]),
        tenant_id=str(row["tenant_id"]),
        kind=str(row["kind"]),  # type: ignore[arg-type]
        resource_id=str(row["resource_id"]),
        target_version=str(row["target_version"]),
        operation=str(row["operation"]),
        requester_actor_id=str(row["requester_actor_id"]),
        status=ApprovalStatus(str(row["status"])),
        approver_actor_id=row["approver_actor_id"],
        reason=row["reason"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        consumed_at=row["consumed_at"],
    )


__all__ = ["ApprovalStorePersistenceError", "PostgresApprovalStore"]
