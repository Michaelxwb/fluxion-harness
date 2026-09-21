from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import TenantContext
from muad_agent_worker.application.delivery_routes import upsert_delivery_route
from muad_agent_worker.infrastructure.models.task import TaskEvent, TaskExecution, TaskSchedule
from muad_contracts import (
    CreateScheduleRequest,
    CreateTaskRequest,
    DeliveryRouteInput,
    ResolvedAgent,
    ResolveDefinitionResponse,
    ResolvedModel,
    ResolvedSkill,
    ScheduleSpec,
    SkillExecutionMode,
    TaskStatus,
)
from sqlalchemy import func, select

SNAPSHOT_HASH = "sha256:" + hashlib.sha256(b"snapshot").hexdigest()


def sample_route(external_user_id: str = "wotv-001") -> DeliveryRouteInput:
    return DeliveryRouteInput(
        channel="WECOM",
        bot_id="bot-demo",
        external_user_id=external_user_id,
    )


def create_task_payload(
    tenant: TenantContext,
    *,
    idempotency_key: str | None = None,
    delivery_route: DeliveryRouteInput | None = None,
    with_route: bool = True,
    skill_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    **overrides: Any,
) -> CreateTaskRequest:
    values: dict[str, Any] = {
        "tenant_id": tenant.tenant_id,
        "agent_id": agent_id or uuid.uuid4(),
        "actor_user_id": actor_user_id or uuid.uuid4(),
        "intent_key": "policy_check",
        "skill_id": skill_id or uuid.uuid4(),
        "skill_artifact_id": uuid.uuid4(),
        "input": {"customers": ["A"]},
        "execution_snapshot": {
            "schema_version": 1,
            "agent": {"key": "agent"},
            "model": {"key": "model"},
            "skills": [{"key": "policy_check"}],
            "mcp": [],
            "prompt_template_version": "v1",
            "budget": {"max_tokens": 1024},
        },
        "snapshot_hash": SNAPSHOT_HASH,
        "idempotency_key": idempotency_key or f"test-{uuid.uuid4()}",
        "delivery_route": delivery_route if delivery_route is not None else (
            sample_route() if with_route else None
        ),
    }
    values.update(overrides)
    return CreateTaskRequest.model_validate(values)


def create_schedule_payload(
    tenant: TenantContext,
    *,
    skill_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    schedule: ScheduleSpec | None = None,
    name: str = "weekday policy check",
    **overrides: Any,
) -> CreateScheduleRequest:
    values: dict[str, Any] = {
        "name": name,
        "agent_id": agent_id or uuid.uuid4(),
        "actor_user_id": actor_user_id or uuid.uuid4(),
        "intent_key": "policy_check",
        "skill_id": skill_id or uuid.uuid4(),
        "input_template": {"customer": "A"},
        "schedule": schedule or ScheduleSpec(type="CRON", cron="0 9 * * *", timezone="Asia/Shanghai"),
        "delivery_route": sample_route(),
    }
    values.update(overrides)
    return CreateScheduleRequest.model_validate(values)


def build_resolve_response(
    skill_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> ResolveDefinitionResponse:
    return ResolveDefinitionResponse(
        agent=ResolvedAgent(
            id=uuid.uuid4(),
            key="agent",
            revision=1,
            instructions="You are helpful.",
            runtime_config={},
        ),
        model=ResolvedModel(
            id=uuid.uuid4(),
            revision=1,
            model_id="gpt-4o-mini",
            base_url="http://model-gateway.internal/v1",
            api_key=None,
            params={},
        ),
        skills=[
            ResolvedSkill(
                skill_id=skill_id,
                artifact_id=artifact_id,
                key="policy-check",
                name="Policy Check",
                description="checks policy",
                version="1.0.0",
                checksum="sha256:" + "1" * 64,
                storage_key=f"skills/{skill_id}/{artifact_id}/skill.zip",
                execution_mode=SkillExecutionMode.ASYNC,
                frontmatter={},
            )
        ],
        mcp_servers=[],
    )


class RecordingExecutor:
    def __init__(
        self,
        result: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[TaskExecution] = []
        self._result = result if result is not None else {"ok": True}
        self._error = error

    async def execute(self, task: TaskExecution) -> dict[str, Any]:
        self.calls.append(task)
        if self._error is not None:
            raise self._error
        return self._result


class FakeResolver:
    def __init__(self, response: ResolveDefinitionResponse) -> None:
        self._response = response
        self.calls: list[tuple[uuid.UUID, uuid.UUID, str]] = []

    async def resolve(
        self,
        agent_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        tenant_id: str,
    ) -> ResolveDefinitionResponse:
        self.calls.append((agent_id, actor_user_id, tenant_id))
        return self._response


async def persist_task(
    tenant: TenantContext,
    *,
    delivery_route: DeliveryRouteInput | None = None,
    **overrides: Any,
) -> TaskExecution:
    now = overrides.pop("now", datetime.now(UTC))
    task_id = uuid.uuid4()
    values: dict[str, Any] = {
        "id": task_id,
        "tenant_id": tenant.tenant_id,
        "agent_id": uuid.uuid4(),
        "actor_user_id": uuid.uuid4(),
        "intent_key": "policy_check",
        "skill_id": uuid.uuid4(),
        "skill_artifact_id": uuid.uuid4(),
        "trigger_type": "IMMEDIATE",
        "execution_mode": "ASYNC",
        "task_type": "SKILL",
        "status": str(TaskStatus.QUEUED),
        "input_json": {"customers": ["A"]},
        "execution_snapshot_schema_version": 1,
        "execution_snapshot_json": {"schema_version": 1},
        "snapshot_hash": SNAPSHOT_HASH,
        "idempotency_key": f"persist-{uuid.uuid4()}",
        "priority": 100,
        "attempt": 0,
        "max_attempts": tenant.settings.task_max_attempts,
        "not_before": now,
        "deadline_at": now + timedelta(hours=tenant.settings.task_default_deadline_hours),
        "delivery_mode": "FINAL_ONLY",
        "delivery_status": "PENDING",
        "delivery_key": f"task:{task_id}:final",
        "delivery_attempts": 0,
    }
    values.update(overrides)
    if values["delivery_mode"] == "NONE":
        values["delivery_status"] = "NONE"
    async with tenant.session_factory() as session:
        async with session.begin():
            if delivery_route is not None:
                values["delivery_route_id"] = await upsert_delivery_route(
                    session,
                    tenant_id=tenant.tenant_id,
                    platform_user_id=values["actor_user_id"],
                    route=delivery_route,
                )
            task = TaskExecution(**values)
            session.add(task)
            await session.flush()
    return task


async def fetch_task(tenant: TenantContext, task_id: uuid.UUID) -> TaskExecution:
    async with tenant.session_factory() as session, session.begin():
        task = await session.get(TaskExecution, task_id)
    if task is None:
        raise AssertionError(f"task {task_id} not found")
    return task


async def fetch_schedule(tenant: TenantContext, schedule_id: uuid.UUID) -> TaskSchedule:
    async with tenant.session_factory() as session, session.begin():
        schedule = await session.get(TaskSchedule, schedule_id)
    if schedule is None:
        raise AssertionError(f"schedule {schedule_id} not found")
    return schedule


async def fetch_events(tenant: TenantContext, task_id: uuid.UUID) -> list[TaskEvent]:
    async with tenant.session_factory() as session, session.begin():
        events = (
            await session.execute(
                select(TaskEvent)
                .where(TaskEvent.task_id == task_id)
                .order_by(TaskEvent.seq.asc())
            )
        ).scalars().all()
    return list(events)


async def count_tasks(tenant: TenantContext, **filters: Any) -> int:
    conditions: list[Any] = [TaskExecution.tenant_id == tenant.tenant_id]
    for name, value in filters.items():
        conditions.append(getattr(TaskExecution, name) == value)
    async with tenant.session_factory() as session, session.begin():
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(TaskExecution).where(*conditions)
                )
            ).scalar_one()
        )
