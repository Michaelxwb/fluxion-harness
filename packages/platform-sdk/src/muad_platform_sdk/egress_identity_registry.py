"""Slow-path PG authority for stable identities and one-time initialization.

Callers authenticate tenant/source ownership before using this internal API.
No per-request accounting or Redis network calls occur in PG transactions.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

import sqlalchemy as sa
from muad_contracts.shared_egress import FrozenIdentityBudgets, FrozenScopeBudget, SharedPolicyModel
from pydantic import Field, ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .egress_identity_models import EgressExecution, EgressInvocation
from .shared_redis import MeteringUnavailable

ScopeKind = Literal["execution", "invocation"]


class IdentityUnavailable(MeteringUnavailable):
    """Identity cannot safely authorize initialization or recovery."""


class ExecutionRegistration(SharedPolicyModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    source_kind: Literal["RUN", "TASK"]
    source_id: uuid.UUID
    meter_epoch: str = Field(min_length=1, max_length=128)
    budget: FrozenIdentityBudgets


@dataclass(frozen=True, slots=True)
class ScopeIdentity:
    id: uuid.UUID
    tenant_id: str
    kind: ScopeKind
    execution_id: uuid.UUID
    state: str
    meter_epoch: str
    budget: FrozenScopeBudget
    fence: int


@dataclass(frozen=True, slots=True)
class InitializationClaim:
    scope: ScopeIdentity
    token: str = field(repr=False)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _budgets(row: EgressExecution) -> FrozenIdentityBudgets:
    if row.is_deleted or row.state in {"BLOCKED", "TERMINAL"}:
        raise IdentityUnavailable("execution is unavailable")
    if row.state in {"INITIALIZING", "READY"} and (
        not row.init_token_hash or len(row.init_token_hash) != 64
    ):
        raise IdentityUnavailable("execution initialization marker is corrupt")
    if _digest(_canonical(row.policy_json)) != row.policy_hash:
        raise IdentityUnavailable("frozen execution budget is corrupt")
    try:
        return FrozenIdentityBudgets.model_validate(row.policy_json)
    except ValidationError as exc:
        raise IdentityUnavailable("frozen execution budget is invalid") from exc


class IdentityRegistry:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncSession]:
        try:
            async with self._sessions.begin() as session:
                yield session
        except SQLAlchemyError as exc:
            raise IdentityUnavailable("egress identity storage unavailable") from exc

    async def _execution(self, session: AsyncSession, tenant_id: str,
                         execution_id: uuid.UUID) -> EgressExecution:
        row = await session.scalar(sa.select(EgressExecution).where(
            EgressExecution.tenant_id == tenant_id, EgressExecution.id == execution_id,
        ).with_for_update())
        if row is None:
            raise IdentityUnavailable("execution is unavailable")
        _budgets(row)
        return row

    async def register_execution(self, request: ExecutionRegistration) -> ScopeIdentity:
        policy = request.budget.model_dump(mode="json")
        digest = _digest(_canonical(policy))
        async with self._transaction() as session:
            await session.execute(pg_insert(EgressExecution).values(
                tenant_id=request.tenant_id, source_kind=request.source_kind, source_id=request.source_id,
                policy_json=policy, policy_hash=digest, meter_epoch=request.meter_epoch,
            ).on_conflict_do_nothing(constraint="uq_egress_execution_source"))
            row = await session.scalar(sa.select(EgressExecution).where(
                EgressExecution.tenant_id == request.tenant_id,
                EgressExecution.source_kind == request.source_kind,
                EgressExecution.source_id == request.source_id,
            ).with_for_update())
            if row is None:
                raise IdentityUnavailable("execution registration unavailable")
            budget = _budgets(row)
            if row.policy_hash != digest or row.meter_epoch != request.meter_epoch:
                raise IdentityUnavailable("frozen execution registration conflicts")
            return ScopeIdentity(row.id, row.tenant_id, "execution", row.id, row.state,
                                 row.meter_epoch, budget.execution, row.current_fence)

    async def register_invocation(self, *, tenant_id: str, execution_id: uuid.UUID,
                                  operation_key: str, skill_key: str) -> ScopeIdentity:
        if not operation_key.strip() or not skill_key.strip():
            raise ValueError("stable operation_key and skill_key are required")
        async with self._transaction() as session:
            parent = await self._execution(session, tenant_id, execution_id)
            budgets = _budgets(parent)
            if parent.state != "READY":
                raise IdentityUnavailable("invocation parent is unavailable")
            await session.execute(pg_insert(EgressInvocation).values(
                tenant_id=tenant_id, execution_id=execution_id,
                operation_key=operation_key, skill_key=skill_key,
            ).on_conflict_do_nothing(constraint="uq_egress_invocation_operation"))
            row = await session.scalar(sa.select(EgressInvocation).where(
                EgressInvocation.tenant_id == tenant_id, EgressInvocation.execution_id == execution_id,
                EgressInvocation.operation_key == operation_key,
            ).with_for_update())
            if (row is None or row.skill_key != skill_key or row.is_deleted
                    or row.state in {"BLOCKED", "TERMINAL"}):
                raise IdentityUnavailable("invocation registration unavailable or conflicts")
            return ScopeIdentity(row.id, tenant_id, "invocation", execution_id, row.state,
                                 parent.meter_epoch, budgets.invocations.get(skill_key, FrozenScopeBudget()),
                                 parent.current_fence)

    async def _locked_scope(
        self, session: AsyncSession, scope: ScopeIdentity,
    ) -> EgressExecution | EgressInvocation:
        parent = await self._execution(session, scope.tenant_id, scope.execution_id)
        if scope.kind == "execution":
            row: EgressExecution | EgressInvocation = parent
            expected: FrozenScopeBudget | None = _budgets(parent).execution
        else:
            invocation = await session.scalar(sa.select(EgressInvocation).where(
                EgressInvocation.tenant_id == scope.tenant_id, EgressInvocation.id == scope.id,
                EgressInvocation.execution_id == parent.id,
            ).with_for_update())
            if invocation is None or parent.state != "READY":
                raise IdentityUnavailable("invocation is unavailable")
            row = invocation
            expected = _budgets(parent).invocations.get(invocation.skill_key, FrozenScopeBudget())
        if (row.id != scope.id or row.is_deleted or row.state in {"BLOCKED", "TERMINAL"}
                or parent.meter_epoch != scope.meter_epoch or expected != scope.budget
                or parent.current_fence != scope.fence):
            raise IdentityUnavailable("identity is unavailable or conflicts")
        return row

    async def claim_initialization(self, scope: ScopeIdentity) -> InitializationClaim | None:
        async with self._transaction() as session:
            row = await self._locked_scope(session, scope)
            if row.state == "READY":
                if not row.init_token_hash or len(row.init_token_hash) != 64:
                    raise IdentityUnavailable("identity initialization marker is corrupt")
                return None
            if row.state != "NEW" or row.init_token_hash is not None:
                raise IdentityUnavailable("identity initialization is uncertain")
            token = secrets.token_urlsafe(32)
            row.state, row.init_token_hash = "INITIALIZING", _digest(token)
            row.update_time = sa.func.clock_timestamp()
            return InitializationClaim(scope, token)

    async def _transition(self, claim: InitializationClaim, state: str) -> bool:
        model = EgressExecution if claim.scope.kind == "execution" else EgressInvocation
        async with self._transaction() as session:
            if state == "READY":
                await self._locked_scope(session, claim.scope)
            result = await session.scalar(sa.update(model).where(
                model.tenant_id == claim.scope.tenant_id, model.id == claim.scope.id,
                model.state == "INITIALIZING", model.is_deleted.is_(False),
                model.init_token_hash == _digest(claim.token),
            ).values(state=state, update_time=sa.func.clock_timestamp()).returning(model.id))
            return result is not None

    async def confirm_initialization(self, claim: InitializationClaim) -> None:
        if not await self._transition(claim, "READY"):
            raise IdentityUnavailable("identity initialization confirmation rejected")

    async def block_initialization(self, claim: InitializationClaim) -> bool:
        return await self._transition(claim, "BLOCKED")
