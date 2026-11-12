"""PG source-lease authentication before host execution-segment activation.

Source owners supply readers that lock their actual row in the supplied session.
Lock order is source row -> egress execution -> fresh PG clock. This slow path
never sends Redis commands or counts business requests inside a PG transaction.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .egress_identity_models import EgressExecution
from .egress_identity_registry import IdentityUnavailable, _budgets
from .egress_segments import SegmentGrant

SourceKind = Literal["RUN", "TASK"]


@dataclass(frozen=True, slots=True)
class SourceClaim:
    tenant_id: str
    source_kind: SourceKind
    source_id: uuid.UUID
    owner: str = field(repr=False)
    epoch: int

    def __post_init__(self) -> None:
        if (not self.tenant_id.strip() or not self.owner.strip() or
                self.source_kind not in {"RUN", "TASK"} or isinstance(self.epoch, bool) or
                not isinstance(self.epoch, int) or self.epoch <= 0 or self.epoch >= 2**53 - 1):
            raise ValueError("trusted source identity, owner and positive epoch required")


@dataclass(frozen=True, slots=True)
class SourceLease:
    tenant_id: str
    source_kind: SourceKind
    source_id: uuid.UUID
    owner: str | None = field(repr=False)
    epoch: int
    status: str
    expires_at: datetime | None
    deadline_at: datetime | None
    cancel_requested: bool
    is_deleted: bool


@dataclass(frozen=True, slots=True)
class AuthorizedSegment:
    grant: SegmentGrant
    local_deadline: float = field(repr=False)

    def remaining_seconds(self) -> float:
        remaining = self.local_deadline - time.monotonic()
        if remaining <= 0:
            raise IdentityUnavailable("source lease expired during authorization")
        return remaining


SourceReader = Callable[[AsyncSession, SourceClaim], Awaitable[SourceLease]]


def _validate_source(lease: SourceLease, claim: SourceClaim) -> None:
    actual = (lease.tenant_id, lease.source_kind, lease.source_id, lease.owner, lease.epoch)
    expected = (claim.tenant_id, claim.source_kind, claim.source_id, claim.owner, claim.epoch)
    if (actual != expected or lease.status != "RUNNING" or lease.cancel_requested or
            lease.is_deleted or lease.expires_at is None or lease.expires_at.tzinfo is None or
            (lease.deadline_at is not None and lease.deadline_at.tzinfo is None)):
        raise IdentityUnavailable("source execution lease lost")


async def _database_now(session: AsyncSession) -> datetime:
    now = await session.scalar(sa.select(sa.func.statement_timestamp()))
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise IdentityUnavailable("source authority clock unavailable")
    return now


class PGSegmentAuthority:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, meter_epoch: str) -> None:
        if not meter_epoch:
            raise ValueError("metering epoch required")
        self._sessions, self._meter_epoch = sessions, meter_epoch

    async def authorize(self, execution_id: uuid.UUID, claim: SourceClaim,
                        reader: SourceReader) -> AuthorizedSegment:
        started = time.monotonic()
        try:
            async with self._sessions.begin() as session:
                lease = await reader(session, claim)
                _validate_source(lease, claim)
                row = await self._execution(session, execution_id, claim)
                now = await _database_now(session)  # Must be AFTER both row locks.
                expires = lease.expires_at
                if expires is None:
                    raise IdentityUnavailable("source execution lease missing")
                deadline = min(expires, lease.deadline_at) if lease.deadline_at is not None else expires
                remaining = (deadline - now).total_seconds()
                if not math.isfinite(remaining) or remaining <= 0:
                    raise IdentityUnavailable("source execution lease expired")
                if row.current_fence > claim.epoch:
                    raise IdentityUnavailable("source fencing epoch is stale")
                row.current_fence, row.update_time = claim.epoch, now
                bound = AuthorizedSegment(self._grant(execution_id, claim), started + remaining)
        except SQLAlchemyError as exc:
            raise IdentityUnavailable("source authority storage unavailable") from exc
        bound.remaining_seconds()  # Commit/lock latency must not extend a source lease.
        return bound

    async def _execution(self, session: AsyncSession, execution_id: uuid.UUID,
                         claim: SourceClaim) -> EgressExecution:
        row = await session.scalar(sa.select(EgressExecution).where(
            EgressExecution.id == execution_id, EgressExecution.tenant_id == claim.tenant_id,
            EgressExecution.source_kind == claim.source_kind, EgressExecution.source_id == claim.source_id,
            EgressExecution.state == "READY", EgressExecution.is_deleted.is_(False),
        ).with_for_update())
        if row is None or row.meter_epoch != self._meter_epoch:
            raise IdentityUnavailable("execution is unavailable to this source")
        _budgets(row)
        return row

    @staticmethod
    def _grant(execution_id: uuid.UUID, claim: SourceClaim) -> SegmentGrant:
        identity = json.dumps([claim.tenant_id, claim.source_kind, str(claim.source_id),
                               claim.owner, claim.epoch], separators=(",", ":"))
        segment = uuid.uuid5(uuid.NAMESPACE_URL, "fluxion-egress-segment:" + identity)
        return SegmentGrant(str(execution_id), claim.epoch, str(segment))
