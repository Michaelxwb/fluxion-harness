"""Isolated Overview browser dataset: real control, Run, model audit and root/child tasks."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

from muad_agent_runtime.infrastructure.models.runtime import Conversation, ModelInvocationAudit, RunRecord
from muad_agent_worker.application.delivery_routes import upsert_delivery_route
from muad_agent_worker.infrastructure.models.task import TaskExecution, TaskSchedule
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ConsoleAccount
from muad_console_platform.infrastructure.models.control import (
    AgentDefinition,
    ModelDefinition,
    PlatformUser,
    Skill,
)
from muad_console_platform.infrastructure.models.mcp import McpServer
from muad_contracts import DeliveryRouteInput
from muad_contracts.enums import RunStatus
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TENANT = os.environ.get("E2E_OVERVIEW_TENANT", "overview-browser")
USERNAME = os.environ.get("E2E_OVERVIEW_USERNAME", "overview-browser-admin")
PASSWORD = os.environ.get("E2E_OVERVIEW_PASSWORD", "overview-browser-password")
AGENT_NAME = "概览浏览器助手"
TABLES = (
    "control.console_account",
    "control.agent_definition",
    "control.skill",
    "control.mcp_server",
    "control.platform_user",
    "control.model_definition",
    "task.task_execution",
    "task.task_schedule",
    "task.delivery_route",
    "runtime.run_record",
    "runtime.conversation",
    "runtime.model_invocation_audit",
)
CLEANUP = (
    "DELETE FROM runtime.model_invocation_audit WHERE tenant_id = :t",
    "DELETE FROM runtime.run_record WHERE tenant_id = :t",
    "DELETE FROM runtime.conversation WHERE tenant_id = :t",
    "DELETE FROM task.task_event WHERE tenant_id = :t",
    "DELETE FROM task.task_submission WHERE tenant_id = :t",
    "DELETE FROM task.task_execution WHERE tenant_id = :t",
    "DELETE FROM task.task_schedule WHERE tenant_id = :t",
    "DELETE FROM task.delivery_route WHERE tenant_id = :t",
    "DELETE FROM control.console_session WHERE account_id IN "
    "(SELECT id FROM control.console_account WHERE tenant_id = :t)",
    "DELETE FROM control.console_account WHERE tenant_id = :t",
    "DELETE FROM control.agent_definition WHERE tenant_id = :t",
    "DELETE FROM control.skill WHERE tenant_id = :t",
    "DELETE FROM control.mcp_server WHERE tenant_id = :t",
    "DELETE FROM control.platform_user WHERE tenant_id = :t",
    "DELETE FROM control.model_definition WHERE tenant_id = :t",
    "DELETE FROM control.config_audit_log WHERE tenant_id = :t",
)


async def _model_and_account(session: AsyncSession) -> ModelDefinition:
    session.add(
        ConsoleAccount(
            tenant_id=TENANT,
            username=USERNAME,
            display_name="Overview Browser Admin",
            password_hash=hash_password(PASSWORD),
            role="ADMIN",
        )
    )
    model = ModelDefinition(
        tenant_id=TENANT,
        key="e2e-overview-model",
        name="Overview Model",
        model_id="overview-model",
        enabled=True,
    )
    session.add(model)
    await session.flush()
    return model


async def _control(session: AsyncSession) -> tuple[AgentDefinition, PlatformUser, Skill]:
    model = await _model_and_account(session)
    agent = AgentDefinition(
        tenant_id=TENANT,
        key="e2e-overview-agent",
        name=AGENT_NAME,
        instructions="Overview fixture",
        model_id=model.id,
        enabled=True,
        revision=1,
    )
    user = PlatformUser(tenant_id=TENANT, user_code="e2e-overview-user", display_name="Overview User")
    skill = Skill(
        tenant_id=TENANT,
        key="e2e-overview-skill",
        name="Overview Skill",
        description="Overview fixture",
        enabled=True,
    )
    session.add_all(
        [
            agent,
            user,
            skill,
            McpServer(
                tenant_id=TENANT,
                key="e2e-overview-mcp",
                name="Overview MCP",
                endpoint="https://example.test/mcp",
                enabled=True,
            ),
        ]
    )
    await session.flush()
    return agent, user, skill


async def _runs(session: AsyncSession, agent: AgentDefinition, user: PlatformUser, now: datetime) -> None:
    for age, status, seconds in ((0, "COMPLETED", 10), (2, "FAILED", 4), (20, "WAITING_TOOL", 0)):
        created = now - timedelta(days=age, minutes=1)
        conversation = Conversation(tenant_id=TENANT, agent_id=agent.id, user_id=user.id)
        session.add(conversation)
        await session.flush()
        run = RunRecord(
            tenant_id=TENANT,
            agent_id=agent.id,
            user_id=user.id,
            conversation_id=conversation.id,
            status=RunStatus(status),
            input_text="Overview user question",
            trace_id=uuid.uuid4().hex,
            create_time=created,
            start_time=created,
            end_time=created + timedelta(seconds=seconds) if seconds else None,
        )
        session.add(run)
        await session.flush()
        requests = (
            [(120, 30, "main", "OK"), (None, None, "main", "RETRY"), (20, 5, "summary", "OK")]
            if age == 0
            else [(0, 0, "main", "ERROR")]
            if age == 2
            else [(7, 3, "main", "OK")]
        )
        _model_requests(session, run, created, requests)


def _model_requests(
    session: AsyncSession,
    run: RunRecord,
    created: datetime,
    requests: list[tuple[int | None, int | None, str, str]],
) -> None:
    for attempt, (inputs, outputs, model, result) in enumerate(requests):
        session.add(
            ModelInvocationAudit(
                tenant_id=TENANT,
                run_id=run.id,
                provider="openai-compatible",
                model=model,
                attempt=attempt,
                status=result,
                input_tokens=inputs,
                output_tokens=outputs,
                latency_ms=50,
                create_time=created + timedelta(seconds=attempt),
            )
        )


def _task(
    agent: AgentDefinition,
    user: PlatformUser,
    skill: Skill,
    now: datetime,
    status: str,
    parent: uuid.UUID | None = None,
) -> TaskExecution:
    task_id = uuid.uuid4()
    return TaskExecution(
        id=task_id,
        tenant_id=TENANT,
        agent_id=agent.id,
        actor_user_id=user.id,
        skill_id=skill.id,
        skill_artifact_id=uuid.uuid4(),
        intent_key="overview_check",
        trigger_type="IMMEDIATE",
        execution_mode="ASYNC",
        task_type="SKILL",
        status=status,
        parent_id=parent,
        create_time=now - timedelta(minutes=1),
        input_json={},
        execution_snapshot_json={},
        snapshot_hash="overview-fixture",
        idempotency_key=task_id.hex,
        deadline_at=now + timedelta(hours=1),
        delivery_mode="FINAL_ONLY",
        delivery_status="FAILED" if status == "FAILED" else "PENDING",
        delivery_key=task_id.hex,
        finished_at=now if status == "FAILED" else None,
    )


async def _tasks(
    session: AsyncSession, agent: AgentDefinition, user: PlatformUser, skill: Skill, now: datetime
) -> None:
    root = _task(agent, user, skill, now, "RUNNING")
    session.add(root)
    await session.flush()
    session.add_all(
        [_task(agent, user, skill, now, "FAILED"), _task(agent, user, skill, now, "FAILED", root.id)]
    )
    route = await upsert_delivery_route(
        session,
        tenant_id=TENANT,
        platform_user_id=user.id,
        route=DeliveryRouteInput(channel="WECOM", bot_id="e2e-overview-bot", external_user_id="e2e-user"),
    )
    session.add(
        TaskSchedule(
            tenant_id=TENANT,
            name="概览浏览器定时",
            agent_id=agent.id,
            actor_user_id=user.id,
            intent_key="overview_check",
            skill_id=skill.id,
            input_template_json={},
            schedule_type="CRON",
            cron_expr="0 9 * * *",
            timezone="Asia/Shanghai",
            delivery_route_id=route,
            status="ACTIVE",
            revision=1,
            next_fire_at=now + timedelta(hours=1),
        )
    )


async def _create() -> None:
    now = datetime.now(UTC)
    async with get_session_factory()() as session:
        async with session.begin():
            agent, user, skill = await _control(session)
            await _runs(session, agent, user, now)
            await _tasks(session, agent, user, skill, now)


async def _cleanup() -> None:
    async with get_session_factory()() as session:
        for statement in CLEANUP:
            await session.execute(text(statement), {"t": TENANT})
        await session.commit()


async def _usage_boundary(action: str) -> None:
    async with get_session_factory()() as session:
        if action == "missing-usage":
            await session.execute(
                text(
                    "UPDATE runtime.model_invocation_audit SET input_tokens = NULL, output_tokens = NULL "
                    "WHERE tenant_id = :t"
                ),
                {"t": TENANT},
            )
        else:
            for statement in CLEANUP[:6]:
                await session.execute(text(statement), {"t": TENANT})
        await session.commit()


async def _counts() -> dict[str, int]:
    async with get_session_factory()() as session:
        result = {}
        for table in TABLES:
            result[table] = int(
                await session.scalar(
                    text("SELECT count(*) FROM " + table + " WHERE tenant_id = :t"), {"t": TENANT}
                )
                or 0
            )
        return result


async def _main(action: str) -> None:
    if action == "create":
        await _cleanup()
        await _create()
    elif action == "cleanup":
        await _cleanup()
    elif action in ("missing-usage", "empty"):
        await _usage_boundary(action)
    elif action == "counts":
        print(json.dumps(await _counts()))
    else:
        raise ValueError("Unknown action: " + action)


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1]))
