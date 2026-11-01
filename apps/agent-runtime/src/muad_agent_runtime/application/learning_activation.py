"""Experience activation: replay gate, state machine, TRIAL lifecycle and audit.

所有状态变更经记忆写入主通道（`MemoryService` 的 `TRANSITION`）：同事务递增版本、
写完整历史、幂等回执与 scope revision，再写审计。自动路径（调度器）按权威 `AGENT_INFERRED`
要求 PASSED 回放；用户显式教学/管理员修订可按权威路径直接激活（带原因）。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from muad_api.audit import write_config_audit
from muad_contracts.platform_settings import MemoryPolicySettings
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactory, get_session_factory
from ..infrastructure.models.runtime import EvalReport, UserMemory
from .memory_commands import MemoryCommand, MemoryMutation, MemoryWriteContext
from .memory_service import MemoryService

ACTIVE = "ACTIVE"
TRIAL = "TRIAL"
PROPOSED = "PROPOSED"
RETIRED = "RETIRED"

PASSED = "PASSED"
BELOW_THRESHOLD = "EXPERIENCE_EVAL_BELOW_THRESHOLD"
MANUAL_REQUIRED = "MANUAL_REQUIRED"
NOT_FOUND = "NOT_FOUND"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
INVALID_TRANSITION = "INVALID_TRANSITION"
UNCHANGED = "UNCHANGED"

EXPERIENCE_CATEGORIES = frozenset({"PROCEDURE", "FAILURE_PATTERN", "VERIFICATION"})
AUDIT_RESOURCE = "MEMORY_EXPERIENCE"

#: 状态机唯一事实源：只允许这些边；RETIRED→PROPOSED 仅由显式重新沉淀使用。
ALLOWED_TRANSITIONS: Mapping[str, frozenset[str]] = {
    PROPOSED: frozenset({TRIAL, ACTIVE, RETIRED}),
    TRIAL: frozenset({ACTIVE, RETIRED}),
    ACTIVE: frozenset({RETIRED, PROPOSED}),
    RETIRED: frozenset({PROPOSED}),
}

#: 需要 PASSED 回放才能激活的权威；USER_EXPLICIT/ADMIN_EDIT 允许直接激活。
REPLAY_GATED_AUTHORITIES = frozenset({"AGENT_INFERRED", "ADMIN_EDIT"})


@dataclass(frozen=True, slots=True)
class ActivationPolicy:
    auto_activate: tuple[str, ...] = ("PROCEDURE", "FAILURE_PATTERN")
    trial_runs: int = 20
    trial_max_regression: float = 0.05


@dataclass(frozen=True, slots=True)
class ActivationOutcome:
    status: str
    experience_key: str
    activation_state: str


def _audit_id(tenant_id: str, user_id: UUID, key: str) -> UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"experience:{tenant_id}:{user_id}:{key}")


def _actor_id(operator: str) -> UUID:
    try:
        return UUID(operator)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"operator:{operator}")


class LearningActivation:
    def __init__(
        self,
        session_factory: SessionFactory | None = None,
        policy: ActivationPolicy | None = None,
    ) -> None:
        self._factory = session_factory or get_session_factory()
        self._policy = policy or ActivationPolicy()
        self._memory = MemoryService(self._factory)

    async def _row(
        self, session: AsyncSession, tenant_id: str, user_id: UUID, key: str
    ) -> UserMemory | None:
        row: UserMemory | None = await session.scalar(
            sa.select(UserMemory)
            .where(
                UserMemory.tenant_id == tenant_id,
                UserMemory.user_id == user_id,
                UserMemory.memory_key == key,
            )
            .with_for_update()
        )
        return row

    async def _replay_passed(
        self,
        session: AsyncSession,
        tenant_id: str,
        user_id: UUID,
        key: str,
        family: str,
        base_config_hash: str,
    ) -> bool:
        row = await session.scalar(
            sa.select(EvalReport)
            .where(
                EvalReport.tenant_id == tenant_id,
                EvalReport.task_family == family,
                EvalReport.experience_key == key,
                EvalReport.experience_user_id == user_id,
                EvalReport.base_config_hash == base_config_hash,
                EvalReport.is_deleted.is_(False),
            )
            .order_by(EvalReport.version.desc())
            .limit(1)
        )
        return row is not None and row.decision_json.get("status") == PASSED

    def _requires_replay(self, authority: str) -> bool:
        return authority in REPLAY_GATED_AUTHORITIES

    async def _transition(
        self,
        tenant_id: str,
        user_id: UUID,
        key: str,
        *,
        target: str,
        action: str,
        operator: str,
        base_config_hash: str,
        reason: str | None = None,
        evidence_updates: Mapping[str, object] | None = None,
    ) -> ActivationOutcome:
        async with self._factory.begin() as session:
            row = await self._row(session, tenant_id, user_id, key)
            if row is None:
                return ActivationOutcome(NOT_FOUND, key, "")
            if row.category not in EXPERIENCE_CATEGORIES:
                return ActivationOutcome(NOT_ELIGIBLE, key, row.activation_state)
            current = row.activation_state
            if current == target:
                return ActivationOutcome(UNCHANGED, key, current)
            if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
                return ActivationOutcome(INVALID_TRANSITION, key, current)
            if evidence_updates:
                evidence: dict[str, Any] = dict(row.evidence_json or {})
                evidence.update(evidence_updates)
                row.evidence_json = evidence
            context = MemoryWriteContext(
                tenant_id=tenant_id,
                user_id=user_id,
                actor_type="SYSTEM" if operator == "automatic" else "ADMIN",
                actor_id=operator,
                settings=MemoryPolicySettings(),
            )
            command = MemoryCommand(
                command_id=f"experience:{tenant_id}:{user_id}:{key}:{action}:{row.version}:{target}",
                endpoint="learning.experience.transition",
                mutations=[
                    MemoryMutation(
                        action="TRANSITION",
                        memory_key=key,
                        expected_version=row.version,
                        activation_state=cast(Any, target),
                        transition_reason=reason,
                    )
                ],
            )
            await self._memory.execute_in_session(session, context, command)
            await write_config_audit(
                session,
                actor_user_id=_actor_id(operator),
                resource_type=AUDIT_RESOURCE,
                resource_id=_audit_id(tenant_id, user_id, key),
                action=action,
                before={"activation_state": current, "category": row.category},
                after={
                    "activation_state": target,
                    "experience_key": key,
                    "user_id": str(user_id),
                    "operator": operator,
                    "reason": reason,
                    "base_config_hash": base_config_hash,
                },
                tenant_id=tenant_id,
            )
        return ActivationOutcome(action, key, target)

    async def activate_manually(
        self,
        tenant_id: str,
        user_id: UUID,
        key: str,
        *,
        base_config_hash: str,
        operator: str,
        reason: str | None = None,
    ) -> ActivationOutcome:
        async with self._factory() as session:
            row = await self._row(session, tenant_id, user_id, key)
            if row is None:
                return ActivationOutcome(NOT_FOUND, key, "")
            family = row.task_signature or ""
            state, authority = row.activation_state, row.authority
            passed = (
                await self._replay_passed(
                    session, tenant_id, user_id, key, family, base_config_hash
                )
                if self._requires_replay(authority)
                else True
            )
        if not passed:
            return ActivationOutcome(BELOW_THRESHOLD, key, state)
        return await self._transition(
            tenant_id,
            user_id,
            key,
            target=ACTIVE,
            action="ACTIVATE",
            operator=operator,
            base_config_hash=base_config_hash,
            reason=reason,
        )

    async def start_trial(
        self,
        tenant_id: str,
        user_id: UUID,
        key: str,
        *,
        base_config_hash: str,
        baseline_corrections: int = 0,
        operator: str = "automatic",
    ) -> ActivationOutcome:
        async with self._factory() as session:
            row = await self._row(session, tenant_id, user_id, key)
            if row is None:
                return ActivationOutcome(NOT_FOUND, key, "")
            if row.category not in self._policy.auto_activate:
                return ActivationOutcome(MANUAL_REQUIRED, key, row.activation_state)
            if not self._requires_replay(row.authority):
                return ActivationOutcome(MANUAL_REQUIRED, key, row.activation_state)
            family = row.task_signature or ""
            passed = await self._replay_passed(
                session, tenant_id, user_id, key, family, base_config_hash
            )
            state = row.activation_state
        if not passed:
            return ActivationOutcome(BELOW_THRESHOLD, key, state)
        return await self._transition(
            tenant_id,
            user_id,
            key,
            target=TRIAL,
            action="TRIAL_START",
            operator=operator,
            base_config_hash=base_config_hash,
            evidence_updates={"trial_baseline_corrections": baseline_corrections},
        )

    async def evaluate_trial(
        self,
        tenant_id: str,
        user_id: UUID,
        key: str,
        *,
        baseline_corrections: int,
        trial_corrections: int,
        base_config_hash: str,
        operator: str = "automatic",
    ) -> ActivationOutcome:
        """TRIAL 期纠正信号不劣化则晋升，否则退休。"""
        if baseline_corrections < 0 or trial_corrections < 0:
            raise ValueError("correction counts must be non-negative")
        baseline_rate = baseline_corrections / max(1, self._policy.trial_runs)
        trial_rate = trial_corrections / max(1, self._policy.trial_runs)
        if trial_rate - baseline_rate > self._policy.trial_max_regression:
            return await self._transition(
                tenant_id,
                user_id,
                key,
                target=RETIRED,
                action="TRIAL_RETIRE",
                operator=operator,
                base_config_hash=base_config_hash,
                reason="trial regression",
            )
        return await self._transition(
            tenant_id,
            user_id,
            key,
            target=ACTIVE,
            action="TRIAL_PROMOTE",
            operator=operator,
            base_config_hash=base_config_hash,
        )

    async def retire(
        self,
        tenant_id: str,
        user_id: UUID,
        key: str,
        *,
        reason: str,
        operator: str,
        base_config_hash: str = "",
    ) -> ActivationOutcome:
        return await self._transition(
            tenant_id,
            user_id,
            key,
            target=RETIRED,
            action="RETIRE",
            operator=operator,
            base_config_hash=base_config_hash,
            reason=reason,
        )
