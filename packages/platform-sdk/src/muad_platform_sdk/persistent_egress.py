"""PG-first initialization and existing-only Redis recovery for trusted hosts."""
from __future__ import annotations

import logging
import uuid
from dataclasses import replace

from .egress_identity_registry import (
    ExecutionRegistration,
    IdentityRegistry,
    IdentityUnavailable,
    ScopeIdentity,
)
from .shared_redis import SharedRedisMeter

logger = logging.getLogger(__name__)


class PersistentEgressScopes:
    def __init__(self, registry: IdentityRegistry, meter: SharedRedisMeter) -> None:
        self._registry, self._meter = registry, meter

    async def execution(self, registration: ExecutionRegistration) -> ScopeIdentity:
        if registration.meter_epoch != self._meter.epoch:
            raise IdentityUnavailable("execution metering epoch conflicts")
        scope = await self._registry.register_execution(registration)
        return await self._ensure(scope)

    async def invocation(self, *, tenant_id: str, execution_id: uuid.UUID,
                         operation_key: str, skill_key: str) -> ScopeIdentity:
        scope = await self._registry.register_invocation(
            tenant_id=tenant_id, execution_id=execution_id, operation_key=operation_key, skill_key=skill_key,
        )
        if scope.meter_epoch != self._meter.epoch:
            raise IdentityUnavailable("invocation metering epoch conflicts")
        return await self._ensure(scope)

    async def _apply(self, scope: ScopeIdentity, *, resume: bool) -> None:
        budget = scope.budget
        if scope.kind == "execution":
            execution_operation = self._meter.resume_execution if resume else self._meter.register_execution
            await execution_operation(str(scope.id), max_requests=budget.max_requests, targets=budget.targets)
        else:
            invocation_operation = (
                self._meter.resume_invocation if resume else self._meter.register_invocation
            )
            await invocation_operation(str(scope.execution_id), str(scope.id),
                            max_requests=budget.max_requests, targets=budget.targets)

    async def _ensure(self, scope: ScopeIdentity) -> ScopeIdentity:
        claim = await self._registry.claim_initialization(scope)
        if claim is None:
            await self._apply(scope, resume=True)
            return replace(scope, state="READY")
        try:
            # PG INITIALIZING is committed before this first Redis creation.
            await self._apply(scope, resume=False)
            await self._registry.confirm_initialization(claim)
        except BaseException:
            try:
                await self._registry.block_initialization(claim)
            except IdentityUnavailable:
                # INITIALIZING also blocks recovery if PG could not mark BLOCKED.
                logger.warning("egress_initialization_block_unconfirmed")
            raise
        return replace(scope, state="READY")
