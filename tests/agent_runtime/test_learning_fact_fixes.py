"""TASK-001: 回放报告用户归属、权威激活与状态主通道（E-02 / E-03）。"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.learning_activation import (
    ACTIVE,
    BELOW_THRESHOLD,
    INVALID_TRANSITION,
    LearningActivation,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    MemoryCommandReceipt,
    RuntimeSnapshot,
    UserMemory,
    UserMemoryHistory,
)
from muad_contracts.memory import RememberMemoryInput
from pydantic import ValidationError

from tests.agent_runtime.learning_wiring_support import (
    cleanup,
    seed_experience,
    seed_report,
    seed_runs,
)


async def _row(tenant: str, user: uuid.UUID, key: str) -> UserMemory:
    async with get_session_factory()() as session:
        row = await session.scalar(
            sa.select(UserMemory).where(
                UserMemory.tenant_id == tenant,
                UserMemory.user_id == user,
                UserMemory.memory_key == key,
            )
        )
    assert row is not None
    return row


async def _counts(tenant: str, user: uuid.UUID, key: str) -> tuple[int, int]:
    async with get_session_factory()() as session:
        history = await session.scalar(
            sa.select(sa.func.count())
            .select_from(UserMemoryHistory)
            .where(
                UserMemoryHistory.tenant_id == tenant,
                UserMemoryHistory.user_id == user,
                UserMemoryHistory.memory_key == key,
            )
        )
        receipts = await session.scalar(
            sa.select(sa.func.count())
            .select_from(MemoryCommandReceipt)
            .where(MemoryCommandReceipt.tenant_id == tenant)
        )
    return int(history or 0), int(receipts or 0)


async def test_e02_authority_paths_borrowed_reports_and_invalid_transition(
    database_guard: None,
) -> None:
    tenant, owner, other = f"facts-{uuid.uuid4()}", uuid.uuid4(), uuid.uuid4()
    agent_key, user_key, retired_key = "exp.agent", "exp.user", "exp.retired"
    await seed_experience(tenant, owner, agent_key, state="PROPOSED")
    await seed_experience(
        tenant, owner, user_key, state="PROPOSED", authority="USER_EXPLICIT", author="USER"
    )
    await seed_experience(
        tenant, owner, retired_key, state="RETIRED", authority="USER_EXPLICIT", author="USER"
    )
    # PASSED 报告属于另一个用户：不得借来激活 owner 的经验。
    await seed_report(tenant, agent_key, "PASSED", user_id=other)
    activation = LearningActivation()
    try:
        borrowed = await activation.activate_manually(
            tenant, owner, agent_key, base_config_hash="cli", operator="automatic"
        )
        assert borrowed.status == BELOW_THRESHOLD
        assert (await _row(tenant, owner, agent_key)).activation_state == "PROPOSED"

        admin = uuid.uuid4()
        activated = await activation.activate_manually(
            tenant, owner, user_key, base_config_hash="cli", operator=str(admin), reason="用户确认"
        )
        assert activated.status == "ACTIVATE"
        row = await _row(tenant, owner, user_key)
        assert row.activation_state == ACTIVE and row.version == 2
        history, receipts = await _counts(tenant, owner, user_key)
        assert history == 1 and receipts >= 1

        async with get_session_factory()() as session:
            audit = await session.execute(
                sa.text(
                    "SELECT actor_user_id FROM control.config_audit_log"
                    " WHERE tenant_id=:tenant AND resource_type='MEMORY_EXPERIENCE'"
                    "   AND after_json->>'experience_key'=:key"
                ),
                {"tenant": tenant, "key": user_key},
            )
            assert audit.scalar_one() == admin

        invalid = await activation.activate_manually(
            tenant, owner, retired_key, base_config_hash="cli", operator="automatic"
        )
        assert invalid.status == INVALID_TRANSITION
        assert (await _row(tenant, owner, retired_key)).activation_state == "RETIRED"

        with pytest.raises((ValidationError, ValueError)):
            RememberMemoryInput.model_validate(
                {"memory_key": "exp.via.admin", "category": "PROCEDURE", "value": "x"}
            )
    finally:
        await cleanup(tenant)


async def test_e03_audit_failure_rolls_back_state_version_history_and_receipt(
    database_guard: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant, user = f"atomic-{uuid.uuid4()}", uuid.uuid4()
    key = "exp.atomic"
    await seed_experience(
        tenant, user, key, state="PROPOSED", authority="USER_EXPLICIT", author="USER"
    )
    await seed_runs(tenant, user, 1)
    async with get_session_factory()() as session:
        snapshot_before = {
            str(row.id): row.policy_json
            for row in (
                await session.scalars(
                    sa.select(RuntimeSnapshot).where(RuntimeSnapshot.tenant_id == tenant)
                )
            ).all()
        }
    activation = LearningActivation()

    async def boom(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(
        "muad_agent_runtime.application.learning_activation.write_config_audit", boom
    )
    try:
        with pytest.raises(RuntimeError):
            await activation.activate_manually(
                tenant, user, key, base_config_hash="cli", operator="automatic"
            )
        row = await _row(tenant, user, key)
        assert row.activation_state == "PROPOSED" and row.version == 1
        history, receipts = await _counts(tenant, user, key)
        assert history == 0 and receipts == 0
        async with get_session_factory()() as session:
            snapshot_after = {
                str(row.id): row.policy_json
                for row in (
                    await session.scalars(
                        sa.select(RuntimeSnapshot).where(RuntimeSnapshot.tenant_id == tenant)
                    )
                ).all()
            }
        assert snapshot_after == snapshot_before
    finally:
        await cleanup(tenant)
