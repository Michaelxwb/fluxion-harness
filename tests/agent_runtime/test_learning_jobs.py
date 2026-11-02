"""TASK-004 / E-04：学习 job 幂等、lease 回收与 INCOMPLETE 不改状态。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from muad_agent_runtime.application.learning_admin_service import LearningAdminService
from muad_agent_runtime.application.learning_jobs import LearningJobRunner
from muad_agent_runtime.application.learning_replay import (
    INCOMPLETE,
    ArmMetrics,
    ReplayDecision,
)
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import LearningJob, UserMemory

from tests.agent_runtime.learning_wiring_support import cleanup, seed_experience


class _Settings:
    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(
            revision=1,
            settings={"learning": {"learning_enabled": True, "eval_min_cases": 1}},
        )


class _Replay:
    def __init__(self, status: str = "PASSED") -> None:
        self.status, self.calls = status, 0

    async def run(self, tenant_id, task_family, **kwargs):
        self.calls += 1
        metrics = ArmMetrics(1, 1, 1, 1)
        return ReplayDecision(self.status, 0.5, metrics, metrics)


async def _job(tenant: str, job_id: uuid.UUID) -> LearningJob:
    async with get_session_factory()() as session:
        job = await session.get(LearningJob, job_id)
    assert job is not None
    return job


async def test_e04_job_idempotency_lease_recovery_and_incomplete(
    database_guard: None,
) -> None:
    tenant, user = f"job-{uuid.uuid4()}", uuid.uuid4()
    await seed_experience(tenant, user, "exp.job", state="PROPOSED")
    service = LearningAdminService(get_session_factory(), settings_client=_Settings())
    replay = _Replay()
    runner = LearningJobRunner(
        get_session_factory(),
        replay,  # type: ignore[arg-type]
        settings_provider=service.learning_settings,
        max_attempts=1,
    )
    try:
        first = await service.enqueue_replay(tenant, user, "exp.job", command_id="cmd-1")
        again = await service.enqueue_replay(tenant, user, "exp.job", command_id="cmd-1")
        assert first.id == again.id
        async with get_session_factory()() as session:
            assert (
                await session.scalar(
                    sa.select(sa.func.count())
                    .select_from(LearningJob)
                    .where(LearningJob.tenant_id == tenant)
                )
                == 1
            )
        assert await runner.run_once() is True
        assert replay.calls == 1
        done = await _job(tenant, first.id)
        assert done.status == "SUCCEEDED" and done.result_json["status"] == "PASSED"
        assert await runner.run_once() is False

        # 崩溃回收：RUNNING + 过期 lease 被重新领取；attempts 超限后 FAILED。
        stale = await service.enqueue_replay(tenant, user, "exp.job", command_id="cmd-2")
        async with get_session_factory()() as session:
            row = await session.get(LearningJob, stale.id)
            assert row is not None
            row.status, row.attempts = "RUNNING", 1
            row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        class _Broken(_Replay):
            async def run(self, *args, **kwargs):
                raise RuntimeError("probe failed")

        broken_runner = LearningJobRunner(
            get_session_factory(),
            _Broken(),  # type: ignore[arg-type]
            settings_provider=service.learning_settings,
            max_attempts=1,
        )
        assert await broken_runner.run_once() is True
        failed = await _job(tenant, stale.id)
        assert failed.status == "FAILED" and failed.error_message == "probe failed"

        incomplete = await service.enqueue_replay(tenant, user, "exp.job", command_id="cmd-3")
        incomplete_runner = LearningJobRunner(
            get_session_factory(),
            _Replay(INCOMPLETE),  # type: ignore[arg-type]
            settings_provider=service.learning_settings,
        )
        assert await incomplete_runner.run_once() is True
        result = await _job(tenant, incomplete.id)
        assert result.result_json["incomplete"] is True
        async with get_session_factory()() as session:
            row = await session.scalar(
                sa.select(UserMemory).where(
                    UserMemory.tenant_id == tenant,
                    UserMemory.user_id == user,
                    UserMemory.memory_key == "exp.job",
                )
            )
        assert row is not None and row.activation_state == "PROPOSED"
    finally:
        await cleanup(tenant)
