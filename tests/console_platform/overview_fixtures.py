"""Real PostgreSQL fixtures for Overview HTTP and metrics tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest
from muad_agent_runtime.infrastructure.models.runtime import Conversation, ModelInvocationAudit, RunRecord
from muad_agent_worker.application.delivery_routes import upsert_delivery_route
from muad_agent_worker.infrastructure.db import get_session_factory as worker_session_factory
from muad_agent_worker.infrastructure.models.task import TaskExecution, TaskSchedule
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import (
    AgentDefinition,
    PlatformSetting,
    PlatformUser,
    Skill,
)
from muad_contracts import DeliveryRouteInput
from muad_contracts.enums import RunStatus
from muad_contracts.platform_settings import default_platform_settings
from sqlalchemy import text

from console_platform.conftest import TenantContext

NOW = datetime(2026, 10, 11, 10, 0, tzinfo=UTC)
WORKER_TENANT_CLEANUP = (
    "DELETE FROM runtime.model_invocation_audit WHERE tenant_id = :t",
    "DELETE FROM runtime.run_record WHERE tenant_id = :t",
    "DELETE FROM runtime.conversation WHERE tenant_id = :t",
    "DELETE FROM task.task_event WHERE tenant_id = :t",
    "DELETE FROM task.task_submission WHERE tenant_id = :t",
    "DELETE FROM task.task_execution WHERE tenant_id = :t",
    "DELETE FROM task.task_schedule WHERE tenant_id = :t",
    "DELETE FROM task.delivery_route WHERE tenant_id = :t",
    "DELETE FROM control.platform_setting WHERE tenant_id = :t",
)


async def _seed_agent(
    tenant: TenantContext, *, name: str, enabled: bool = True, is_deleted: bool = False
) -> uuid.UUID:
    agent_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            AgentDefinition(
                id=agent_id,
                tenant_id=tenant.tenant_id,
                key=f"agent-{agent_id.hex[:8]}",
                name=name,
                instructions="overview fixture",
                model_id=tenant.model_id,
                revision=1,
                enabled=enabled,
                is_deleted=is_deleted,
            )
        )
        await session.commit()
    return agent_id


async def _seed_skill(tenant: TenantContext, *, enabled: bool = True) -> uuid.UUID:
    skill_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Skill(
                id=skill_id,
                tenant_id=tenant.tenant_id,
                key=f"skill-{skill_id.hex[:8]}",
                name="Overview Skill",
                description="overview fixture",
                enabled=enabled,
            )
        )
        await session.commit()
    return skill_id


async def _seed_platform_user(tenant: TenantContext, *, display_name: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            PlatformUser(
                id=user_id,
                tenant_id=tenant.tenant_id,
                user_code=f"u-{user_id.hex[:8]}",
                display_name=display_name,
            )
        )
        await session.commit()
    return user_id


async def _seed_task(
    tenant: TenantContext,
    *,
    agent_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    status: str = "QUEUED",
    create_time: datetime | None = None,
) -> uuid.UUID:
    task_id = uuid.uuid4()
    async with worker_session_factory()() as session:
        session.add(
            TaskExecution(
                id=task_id,
                tenant_id=tenant.tenant_id,
                agent_id=agent_id,
                actor_user_id=actor_user_id,
                intent_key="policy_check",
                skill_id=uuid.uuid4(),
                skill_artifact_id=uuid.uuid4(),
                trigger_type="SCHEDULED",
                execution_mode="ASYNC",
                task_type="SKILL",
                status=status,
                input_json={},
                execution_snapshot_schema_version=1,
                execution_snapshot_json={"schema_version": 1},
                snapshot_hash="sha256:" + "d" * 64,
                idempotency_key=f"overview-{task_id}",
                priority=100,
                attempt=0,
                max_attempts=3,
                not_before=NOW,
                deadline_at=NOW + timedelta(hours=24),
                started_at=NOW,
                delivery_mode="FINAL_ONLY",
                delivery_status="PENDING",
                delivery_key=f"task:{task_id}:final",
                delivery_attempts=0,
                create_time=create_time or NOW,
            )
        )
        await session.commit()
    return task_id


async def _seed_schedule(
    tenant: TenantContext,
    *,
    agent_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    name: str,
    status: str = "ACTIVE",
    next_fire_at: datetime | None = None,
) -> uuid.UUID:
    schedule_id = uuid.uuid4()
    async with worker_session_factory()() as session:
        async with session.begin():
            route_id = await upsert_delivery_route(
                session,
                tenant_id=tenant.tenant_id,
                platform_user_id=actor_user_id,
                route=DeliveryRouteInput(
                    channel="WECOM",
                    bot_id=f"overview-bot-{schedule_id.hex[:8]}",
                    external_user_id="overview-user",
                ),
            )
            session.add(
                TaskSchedule(
                    id=schedule_id,
                    tenant_id=tenant.tenant_id,
                    name=name,
                    agent_id=agent_id,
                    actor_user_id=actor_user_id,
                    intent_key="policy_check",
                    skill_id=uuid.uuid4(),
                    input_template_json={},
                    schedule_type="CRON",
                    cron_expr="0 9 * * *",
                    timezone="Asia/Shanghai",
                    delivery_route_id=route_id,
                    status=status,
                    next_fire_at=next_fire_at,
                    revision=1,
                )
            )
    return schedule_id


@pytest.fixture
async def overview_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    """`tenant` 夹具不清理 task schema；本夹具补齐 task.* 的收尾清理。"""

    async def cleanup() -> None:
        async with worker_session_factory()() as session:
            for tenant_id in (tenant.tenant_id, tenant.tenant_id + "-other"):
                for statement in WORKER_TENANT_CLEANUP:
                    await session.execute(text(statement), {"t": tenant_id})
            await session.execute(
                text("DELETE FROM control.agent_definition WHERE tenant_id = :t"),
                {"t": tenant.tenant_id + "-other"},
            )
            await session.commit()

    await cleanup()
    try:
        yield tenant
    finally:
        await cleanup()


async def seed_run(
    tenant: TenantContext,
    *,
    status: str = "COMPLETED",
    created: datetime = NOW,
    start: datetime | None = None,
    end: datetime | None = None,
    agent: uuid.UUID | None = None,
    user: uuid.UUID | None = None,
    deleted: bool = False,
) -> uuid.UUID:
    agent_id, user_id = agent or uuid.uuid4(), user or uuid.uuid4()
    conversation_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Conversation(id=conversation_id, tenant_id=tenant.tenant_id, agent_id=agent_id, user_id=user_id)
        )
        await session.flush()
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                user_id=user_id,
                status=RunStatus(status),
                input_text="overview input",
                trace_id=run_id.hex,
                create_time=created,
                start_time=start,
                end_time=end,
                is_deleted=deleted,
            )
        )
        await session.commit()
    return run_id


async def seed_model(
    tenant: TenantContext,
    run_id: uuid.UUID,
    *,
    inputs: int | None = None,
    outputs: int | None = None,
    status: str = "OK",
    model: str = "main",
    created: datetime = NOW,
    deleted: bool = False,
    attempt: int = 0,
) -> None:
    async with get_session_factory()() as session:
        session.add(
            ModelInvocationAudit(
                tenant_id=tenant.tenant_id,
                run_id=run_id,
                provider="openai-compatible",
                model=model,
                attempt=attempt,
                status=status,
                input_tokens=inputs,
                output_tokens=outputs,
                latency_ms=25,
                create_time=created,
                is_deleted=deleted,
            )
        )
        await session.commit()


async def seed_task(
    tenant: TenantContext,
    *,
    status: str = "QUEUED",
    created: datetime = NOW,
    finished: datetime | None = None,
    parent: uuid.UUID | None = None,
    deleted: bool = False,
    delivery: str = "PENDING",
    mode: str = "FINAL_ONLY",
    deadline: datetime | None = None,
) -> uuid.UUID:
    task_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            TaskExecution(
                id=task_id,
                tenant_id=tenant.tenant_id,
                agent_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
                intent_key="overview",
                skill_id=uuid.uuid4(),
                skill_artifact_id=uuid.uuid4(),
                trigger_type="IMMEDIATE",
                execution_mode="ASYNC",
                task_type="SKILL",
                status=status,
                parent_id=parent,
                input_json={},
                execution_snapshot_json={},
                snapshot_hash="overview-hash",
                idempotency_key=task_id.hex,
                delivery_key=task_id.hex,
                delivery_status=delivery,
                delivery_mode=mode,
                create_time=created,
                finished_at=finished,
                is_deleted=deleted,
                deadline_at=deadline or NOW + timedelta(days=1),
            )
        )
        await session.commit()
    return task_id


async def seed_timezone(tenant: TenantContext, timezone: str) -> None:
    document = asdict(default_platform_settings())
    document["locale"]["default_timezone"] = timezone
    async with get_session_factory()() as session:
        session.add(
            PlatformSetting(
                tenant_id=tenant.tenant_id, revision=1, settings_json=document, actor_user_id=uuid.uuid4()
            )
        )
        await session.commit()
