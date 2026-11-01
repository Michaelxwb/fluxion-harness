"""Experience activation: replay gate, TRIAL promotion/retirement and audit.

显式激活（`learning_admin` 或装配层）在回放达标后直接进入 ACTIVE；自动路径先进 TRIAL，
TRIAL 期纠正信号不劣化才晋升、劣化自动退休。每次状态变化写 control.config_audit_log。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from muad_api.audit import write_config_audit
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactory, get_session_factory
from ..infrastructure.models.runtime import EvalReport, UserMemory

ACTIVE = "ACTIVE"
TRIAL = "TRIAL"
PROPOSED = "PROPOSED"
RETIRED = "RETIRED"

PASSED = "PASSED"
BELOW_THRESHOLD = "EXPERIENCE_EVAL_BELOW_THRESHOLD"
MANUAL_REQUIRED = "MANUAL_REQUIRED"
NOT_FOUND = "NOT_FOUND"
NOT_ELIGIBLE = "NOT_ELIGIBLE"

EXPERIENCE_CATEGORIES = frozenset({"PROCEDURE", "FAILURE_PATTERN", "VERIFICATION"})
AUDIT_RESOURCE = "MEMORY_EXPERIENCE"


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


def _audit_id(key: str) -> UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"experience:{key}")


class LearningActivation:
    def __init__(
        self,
        session_factory: SessionFactory | None = None,
        policy: ActivationPolicy | None = None,
    ) -> None:
        self._factory = session_factory or get_session_factory()
        self._policy = policy or ActivationPolicy()

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
        self, session: AsyncSession, tenant_id: str, key: str, family: str, base_config_hash: str
    ) -> bool:
        row = await session.scalar(
            sa.select(EvalReport)
            .where(
                EvalReport.tenant_id == tenant_id,
                EvalReport.task_family == family,
                EvalReport.experience_key == key,
                EvalReport.base_config_hash == base_config_hash,
                EvalReport.is_deleted.is_(False),
            )
            .order_by(EvalReport.version.desc())
            .limit(1)
        )
        return row is not None and row.decision_json.get("status") == PASSED

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
    ) -> ActivationOutcome:
        async with self._factory.begin() as session:
            row = await self._row(session, tenant_id, user_id, key)
            if row is None:
                return ActivationOutcome(NOT_FOUND, key, "")
            if row.category not in EXPERIENCE_CATEGORIES:
                return ActivationOutcome(NOT_ELIGIBLE, key, row.activation_state)
            before = {"activation_state": row.activation_state, "category": row.category}
            row.activation_state = target
            await write_config_audit(
                session,
                actor_user_id=uuid.uuid5(uuid.NAMESPACE_URL, f"operator:{operator}"),
                resource_type=AUDIT_RESOURCE,
                resource_id=_audit_id(key),
                action=action,
                before=before,
                after={
                    "activation_state": target,
                    "experience_key": key,
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
    ) -> ActivationOutcome:
        async with self._factory() as session:
            row = await self._row(session, tenant_id, user_id, key)
            if row is None:
                return ActivationOutcome(NOT_FOUND, key, "")
            family = row.task_signature or ""
            passed = await self._replay_passed(
                session, tenant_id, key, family, base_config_hash
            )
            state = row.activation_state
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
            family = row.task_signature or ""
            passed = await self._replay_passed(
                session, tenant_id, key, family, base_config_hash
            )
            state = row.activation_state
        if not passed:
            return ActivationOutcome(BELOW_THRESHOLD, key, state)
        async with self._factory.begin() as session:
            row = await self._row(session, tenant_id, user_id, key)
            if row is not None:
                evidence = dict(row.evidence_json or {})
                evidence["trial_baseline_corrections"] = baseline_corrections
                row.evidence_json = evidence
        return await self._transition(
            tenant_id,
            user_id,
            key,
            target=TRIAL,
            action="TRIAL_START",
            operator=operator,
            base_config_hash=base_config_hash,
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
