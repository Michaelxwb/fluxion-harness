"""概览 E2E 的种子：可登录的 Console 管理员 + 一小组可辨识的业务数据。

概览是**只读聚合**，浏览器侧的 S-02/S-03/S-04 需要页面上真有行可点、可跳，故种子包含：
启用 Agent、启用 Skill、一条最近 Task、一条 ACTIVE 且带 `next_fire_at` 的 Schedule。
E-03/E-02/E-04 走失败路径（拦断或软删目标），不需要更多数据。

租户由 `E2E_OVERVIEW_TENANT` 指定，须与 Console 的 `DEFAULT_TENANT_ID` 一致
（浏览器请求不带 X-Tenant-Id，Console 回落默认租户）。

CLI：`create` 建账号+业务数据 / `cleanup` 清干净 / `counts` 打印各表行数（供收尾断言）。
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

from muad_agent_worker.application.delivery_routes import upsert_delivery_route
from muad_agent_worker.infrastructure.models.task import TaskExecution, TaskSchedule
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount
from muad_console_platform.infrastructure.models.control import (
    AgentDefinition,
    ModelDefinition,
    PlatformUser,
    Skill,
)
from muad_contracts import DeliveryRouteInput
from sqlalchemy import text

TENANT = os.environ.get("E2E_OVERVIEW_TENANT", "overview-browser")
USERNAME = os.environ.get("E2E_OVERVIEW_USERNAME", "overview-browser-admin")
PASSWORD = os.environ.get("E2E_OVERVIEW_PASSWORD", "overview-browser-password")

AGENT_NAME = "概览浏览器助手"
SCHEDULE_NAME = "概览浏览器定时"
TASK_INTENT = "overview_browser_check"

TABLES = (
    "control.console_account",
    "control.agent_definition",
    "control.skill",
    "control.platform_user",
    "control.model_definition",
    "task.task_execution",
    "task.task_schedule",
    "task.delivery_route",
)

CLEANUP = (
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
    "DELETE FROM control.platform_user WHERE tenant_id = :t",
    "DELETE FROM control.model_definition WHERE tenant_id = :t",
)


async def _create() -> None:
    now = datetime.now(UTC)
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            session.add(
                ConsoleAccount(
                    tenant_id=TENANT,
                    username=USERNAME,
                    display_name="Overview Browser Admin",
                    password_hash=hash_password(PASSWORD),
                    role=ROLE_ADMIN,
                )
            )
            model = ModelDefinition(
                tenant_id=TENANT,
                key=f"e2e-model-{uuid.uuid4().hex[:8]}",
                name="E2E Model",
                model_id="gpt-4o-mini",
                base_url="https://api.example.com/v1",
                enabled=True,
            )
            session.add(model)
            await session.flush()
            agent = AgentDefinition(
                tenant_id=TENANT,
                key=f"e2e-agent-{uuid.uuid4().hex[:8]}",
                name=AGENT_NAME,
                instructions="overview e2e",
                model_id=model.id,
                revision=1,
                enabled=True,
            )
            session.add(agent)
            session.add(
                Skill(
                    tenant_id=TENANT,
                    key=f"e2e-skill-{uuid.uuid4().hex[:8]}",
                    name="E2E Skill",
                    description="overview e2e",
                    enabled=True,
                )
            )
            user = PlatformUser(
                tenant_id=TENANT,
                user_code=f"e2e-u-{uuid.uuid4().hex[:8]}",
                display_name="E2E Actor",
            )
            session.add(user)
            await session.flush()
            task_id = uuid.uuid4()
            session.add(
                TaskExecution(
                    id=task_id,
                    tenant_id=TENANT,
                    agent_id=agent.id,
                    actor_user_id=user.id,
                    intent_key=TASK_INTENT,
                    skill_id=uuid.uuid4(),
                    skill_artifact_id=uuid.uuid4(),
                    trigger_type="IMMEDIATE",
                    execution_mode="ASYNC",
                    task_type="SKILL",
                    status="RUNNING",
                    input_json={},
                    execution_snapshot_schema_version=1,
                    execution_snapshot_json={"schema_version": 1},
                    snapshot_hash="sha256:" + "e" * 64,
                    idempotency_key=f"e2e-overview-{task_id}",
                    priority=100,
                    attempt=0,
                    max_attempts=3,
                    not_before=now,
                    deadline_at=now + timedelta(hours=24),
                    started_at=now,
                    delivery_mode="NONE",
                    delivery_status="NONE",
                    delivery_key=f"task:{task_id}:final",
                    delivery_attempts=0,
                )
            )
            route_id = await upsert_delivery_route(
                session,
                tenant_id=TENANT,
                platform_user_id=user.id,
                route=DeliveryRouteInput(
                    channel="WECOM",
                    bot_id=f"e2e-overview-bot-{uuid.uuid4().hex[:8]}",
                    external_user_id="e2e-overview",
                ),
            )
            session.add(
                TaskSchedule(
                    tenant_id=TENANT,
                    name=SCHEDULE_NAME,
                    agent_id=agent.id,
                    actor_user_id=user.id,
                    intent_key=TASK_INTENT,
                    skill_id=uuid.uuid4(),
                    input_template_json={},
                    schedule_type="CRON",
                    cron_expr="0 9 * * *",
                    timezone="Asia/Shanghai",
                    delivery_route_id=route_id,
                    status="ACTIVE",
                    next_fire_at=now + timedelta(hours=2),
                    revision=1,
                )
            )
    print(f"created {USERNAME}@{TENANT} + 1 agent/skill/task/schedule")


async def _cleanup() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        for statement in CLEANUP:
            await session.execute(text(statement), {"t": TENANT})
        await session.commit()
    print(f"cleaned {TENANT}")


async def _counts() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        parts = []
        for table in TABLES:
            value = await session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": TENANT}
            )
            parts.append(f"{table.split('.')[-1]}={int(value or 0)}")
    print(" ".join(parts))


async def _run(action: str) -> int:
    if action == "create":
        await _cleanup()
        await _create()
    elif action == "cleanup":
        await _cleanup()
    elif action == "counts":
        await _counts()
    else:
        raise SystemExit(f"unknown action: {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run(sys.argv[1] if len(sys.argv) > 1 else "create")))
