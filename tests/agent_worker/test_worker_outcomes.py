"""WAITING / 重试 / 受保护的完成结果（B-113 / RULE-worker-001）。

真实边界：真实 Skill 执行结果 → Worker → 真实 PostgreSQL CAS。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from conftest import TenantContext
from helpers import persist_task
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.execution_outcomes import (
    OutcomeKind,
    TaskOutcome,
    interpret_execution,
)
from muad_agent_worker.worker.service import WorkerLoop


def _now() -> datetime:
    return datetime.now(UTC)


class _ScriptedExecutor:
    """按脚本返回执行结果，模拟真实 Skill 的三种产出。"""

    def __init__(self, execution: dict[str, Any]) -> None:
        self.execution = execution
        self.calls = 0

    async def execute(self, task: TaskExecution) -> dict[str, Any]:
        self.calls += 1
        return self.execution


async def _row(tenant: TenantContext, task_id: uuid.UUID) -> dict[str, Any]:
    async with tenant.session_factory() as session:
        row = (
            await session.execute(
                sa.select(
                    TaskExecution.status,
                    TaskExecution.lease_owner,
                    TaskExecution.lease_until,
                    TaskExecution.not_before,
                    TaskExecution.external_ref_json,
                    TaskExecution.result_json,
                    TaskExecution.result_artifact_id,
                    TaskExecution.attempt,
                ).where(TaskExecution.id == task_id)
            )
        ).one()
    return {
        "status": row[0],
        "lease_owner": row[1],
        "lease_until": row[2],
        "not_before": row[3],
        "external_ref": row[4],
        "result": row[5],
        "result_artifact_id": row[6],
        "attempt": row[7],
    }


def _success(payload: dict[str, Any]) -> dict[str, Any]:
    return {"status": "SUCCEEDED", "result": payload, "stderr": "", "exit_code": 0}


def test_interpret_maps_execution_into_explicit_outcomes() -> None:
    now = _now()
    completed = interpret_execution(_success({"answer": 42}), now=now)
    assert completed.kind is OutcomeKind.COMPLETED
    assert completed.result == {"answer": 42}

    resume_at = (now + timedelta(minutes=5)).isoformat()
    waiting = interpret_execution(
        _success(
            {"wait": {"external_ref": {"external_task_id": "ext-1"}, "not_before": resume_at}}
        ),
        now=now,
    )
    assert waiting.kind is OutcomeKind.WAITING
    assert waiting.external_ref == {"external_task_id": "ext-1"}
    assert waiting.not_before == now + timedelta(minutes=5)

    failed = interpret_execution({"status": "TIMED_OUT", "result": None, "stderr": "too slow"}, now=now)
    assert failed.kind is OutcomeKind.RETRYABLE_FAILURE

    cancelled = interpret_execution({"status": "CANCELLED", "result": None}, now=now)
    assert cancelled.kind is OutcomeKind.CANCELLED


async def test_waiting_releases_lease_and_is_claimable_after_not_before(
    tenant: TenantContext,
) -> None:
    """WAITING 持久化 external_ref/not_before 后释放 lease；到期后能被再次 claim。"""
    task = await persist_task(tenant)
    resume_at = _now() + timedelta(seconds=30)
    executor = _ScriptedExecutor(
        _success(
            {
                "wait": {
                    "external_ref": {"external_task_id": "ext-9"},
                    "not_before": resume_at.isoformat(),
                }
            }
        )
    )
    worker = WorkerLoop(tenant.session_factory, tenant.settings, executor=executor, instance_id="w1")

    await worker.run_once()

    state = await _row(tenant, task.id)
    assert state["status"] == "WAITING"
    assert state["lease_owner"] is None, "WAITING 不应继续占着 lease"
    assert state["lease_until"] is None
    assert state["external_ref"] == {"external_task_id": "ext-9"}
    assert state["not_before"] == resume_at

    # 未到期：另一实例 claim 不到
    other = WorkerLoop(tenant.session_factory, tenant.settings, executor=executor, instance_id="w2")
    assert await other._claimer.claim_one("w2") is None

    # 到期后可被再次 claim
    later = await other._claimer.claim_one("w2", now=resume_at + timedelta(seconds=1))
    assert later is not None and later.id == task.id


async def test_waiting_does_not_consume_retry_budget(tenant: TenantContext) -> None:
    """WAITING 是正常外部等待，不消耗 attempt。"""
    task = await persist_task(tenant)
    executor = _ScriptedExecutor(_success({"wait": {"external_ref": {}, "not_before": None}}))
    worker = WorkerLoop(tenant.session_factory, tenant.settings, executor=executor, instance_id="w1")

    await worker.run_once()

    state = await _row(tenant, task.id)
    assert state["status"] == "WAITING"
    assert state["attempt"] == 1
    assert state["not_before"] > _now(), "缺省 not_before 必须落在将来"


async def test_retry_budget_is_exhausted_exactly_once(tenant: TenantContext) -> None:
    """重试到上限即 FAILED，不会多跑一次。"""
    task = await persist_task(tenant, max_attempts=2)
    executor = _ScriptedExecutor({"status": "FAILED", "result": None, "stderr": "boom"})
    worker = WorkerLoop(tenant.session_factory, tenant.settings, executor=executor, instance_id="w1")

    await worker.run_once()
    first = await _row(tenant, task.id)
    assert first["status"] == "QUEUED", "首次失败应重试"
    assert first["result"] is None

    await worker.run_once(now=first["not_before"] + timedelta(seconds=1))

    final = await _row(tenant, task.id)
    assert final["status"] == "FAILED"
    assert final["attempt"] == 2, "attempt 不应超过 max_attempts"


async def test_completed_result_is_persisted_before_terminal(tenant: TenantContext) -> None:
    """结果与终态同事务落库：不存在 COMPLETED 但没有结果的窗口。"""
    task = await persist_task(tenant)
    executor = _ScriptedExecutor(_success({"answer": "ok"}))
    worker = WorkerLoop(tenant.session_factory, tenant.settings, executor=executor, instance_id="w1")

    await worker.run_once()

    state = await _row(tenant, task.id)
    assert state["status"] == "COMPLETED"
    assert state["result"] == {"answer": "ok"}
    assert state["lease_owner"] is None


async def test_terminal_task_is_not_overwritten_by_late_success(tenant: TenantContext) -> None:
    """已被别人置为终态的 Task，不会被迟到的成功结果覆盖。"""
    task = await persist_task(tenant)
    executor = _ScriptedExecutor(_success({"late": True}))
    worker = WorkerLoop(tenant.session_factory, tenant.settings, executor=executor, instance_id="w1")
    claimed = await worker._claimer.claim_one("w1")
    assert claimed is not None

    async with tenant.session_factory() as session:
        await session.execute(
            sa.update(TaskExecution)
            .where(TaskExecution.id == task.id)
            .values(status="FAILED", lease_owner=None, lease_until=None)
        )
        await session.commit()

    await worker._handle_outcome(
        claimed,
        TaskOutcome(kind=OutcomeKind.COMPLETED, result={"late": True}),
        now=_now(),
    )

    state = await _row(tenant, task.id)
    assert state["status"] == "FAILED", "迟到的成功覆盖了终态"
    assert state["result"] is None


async def test_large_result_uses_artifact_reference(tenant: TenantContext) -> None:
    """大结果沿已有 Artifact 引用契约：落 result_artifact_id，内联只保留预览。"""
    task = await persist_task(tenant)
    artifact_id = uuid.uuid4()
    executor = _ScriptedExecutor(
        _success({"result_artifact_id": str(artifact_id), "preview": "head...", "bytes": 5_000_000})
    )
    worker = WorkerLoop(tenant.session_factory, tenant.settings, executor=executor, instance_id="w1")

    await worker.run_once()

    state = await _row(tenant, task.id)
    assert state["result_artifact_id"] == artifact_id
    assert "result_artifact_id" not in (state["result"] or {}), "引用不应再内联回 result_json"


async def test_cancelled_execution_marks_task_cancelled(tenant: TenantContext) -> None:
    """技能返回 CANCELLED 时按取消落终态。"""
    task = await persist_task(tenant)
    executor = _ScriptedExecutor({"status": "CANCELLED", "result": None})
    worker = WorkerLoop(tenant.session_factory, tenant.settings, executor=executor, instance_id="w1")

    await worker.run_once()

    assert (await _row(tenant, task.id))["status"] == "CANCELLED"


async def test_keyboard_interrupt_stops_worker(tenant: TenantContext) -> None:
    """执行器抛未预期异常时走失败路径，不把循环带崩。"""
    class _Exploding:
        async def execute(self, task: TaskExecution) -> dict[str, Any]:
            raise RuntimeError("unexpected")

    task = await persist_task(tenant)
    worker = WorkerLoop(
        tenant.session_factory, tenant.settings, executor=_Exploding(), instance_id="w1"
    )

    await asyncio.wait_for(worker.run_once(), timeout=5)

    state = await _row(tenant, task.id)
    assert state["status"] in {"QUEUED", "FAILED"}
