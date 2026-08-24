from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from fluxion.resources import ResourceKind


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    approval_id: str
    tenant_id: str
    kind: ResourceKind
    resource_id: str
    target_version: str
    operation: str
    requester_actor_id: str
    status: ApprovalStatus
    approver_actor_id: str | None
    reason: str | None
    expires_at: datetime
    created_at: datetime
    decided_at: datetime | None


class ApprovalStore(Protocol):
    async def create(self, record: ApprovalRecord) -> ApprovalRecord: ...

    async def get(self, approval_id: str, *, tenant_id: str) -> ApprovalRecord | None: ...

    async def decide(
        self,
        approval_id: str,
        *,
        tenant_id: str,
        approver_actor_id: str,
        approved: bool,
        reason: str | None,
        decided_at: datetime,
    ) -> ApprovalRecord: ...


class InMemoryApprovalStore:
    """进程内审批决策存储。

    与 trace/secret 等 in-memory store 保持一致；审批决策不跨进程持久化，
    重启后需重新签发。生产多实例应替换为 DB 或外部审批服务实现。
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], ApprovalRecord] = {}

    async def create(self, record: ApprovalRecord) -> ApprovalRecord:
        key = (record.tenant_id, record.approval_id)
        if key in self._records:
            raise ValueError(f"approval {record.approval_id} already exists")
        self._records[key] = record
        return record

    async def get(self, approval_id: str, *, tenant_id: str) -> ApprovalRecord | None:
        return self._records.get((tenant_id, approval_id))

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
        key = (tenant_id, approval_id)
        record = self._records.get(key)
        if record is None:
            raise KeyError(approval_id)
        if record.status is not ApprovalStatus.PENDING:
            raise ValueError(f"approval {approval_id} already decided")
        decided = ApprovalRecord(
            approval_id=record.approval_id,
            tenant_id=record.tenant_id,
            kind=record.kind,
            resource_id=record.resource_id,
            target_version=record.target_version,
            operation=record.operation,
            requester_actor_id=record.requester_actor_id,
            status=ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED,
            approver_actor_id=approver_actor_id,
            reason=reason or record.reason,
            expires_at=record.expires_at,
            created_at=record.created_at,
            decided_at=decided_at,
        )
        self._records[key] = decided
        return decided


def new_approval_id() -> str:
    return f"approval_{uuid4().hex}"


def default_expiry(now: datetime, ttl_seconds: float) -> datetime:
    return now + timedelta(seconds=ttl_seconds)


def utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "ApprovalRecord",
    "ApprovalStatus",
    "ApprovalStore",
    "InMemoryApprovalStore",
    "default_expiry",
    "new_approval_id",
    "utc_now",
]
