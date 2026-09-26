"""[E-01][RULE-api-001][RULE-time-001] 概览聚合 API-01（`GET /api/v1/overview`）。

真实边界：真实 Console HTTP（ASGI 全栈 + 登录会话/CSRF）→ 真实 PostgreSQL
（`control.{agent_definition,skill,platform_user}` 与 `task.{task_execution,task_schedule}`
逐行回读）；不 mock 业务 API。

E-01 的要点是**局部缺失不得让整体判错**：某模块无数据时对应 KPI=0、列表为空，
HTTP 仍是 200 且封套 `code="0"`。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from muad_agent_worker.application.delivery_routes import upsert_delivery_route
from muad_agent_worker.infrastructure.db import get_session_factory as worker_session_factory
from muad_agent_worker.infrastructure.models.task import TaskExecution, TaskSchedule
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import (
    AgentDefinition,
    PlatformUser,
    Skill,
)
from muad_console_platform.main import app
from muad_contracts import DeliveryRouteInput
from sqlalchemy import text

from console_platform.conftest import TenantContext

ENVELOPE_KEYS = {"code", "msg", "data", "trace_id", "request_id", "timestamp"}
KPI_KEYS = {"enabled_agents", "enabled_skills", "active_tasks", "active_schedules"}
TASK_ITEM_KEYS = {
    "task_id",
    "intent_key",
    "agent_id",
    "agent_name",
    "actor_user_id",
    "actor_user_name",
    "status",
    "trigger_type",
    "delivery_status",
    "started_at",
    "finished_at",
    "deadline_at",
    "create_time",
}
SCHEDULE_ITEM_KEYS = {
    "schedule_id",
    "name",
    "agent_id",
    "agent_name",
    "actor_user_id",
    "actor_user_name",
    "intent_key",
    "status",
    "next_fire_at",
    "last_fire_at",
    "timezone",
}
CONSOLE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

WORKER_TENANT_CLEANUP = (
    "DELETE FROM task.task_event WHERE tenant_id = :t",
    "DELETE FROM task.task_submission WHERE tenant_id = :t",
    "DELETE FROM task.task_execution WHERE tenant_id = :t",
    "DELETE FROM task.task_schedule WHERE tenant_id = :t",
    "DELETE FROM task.delivery_route WHERE tenant_id = :t",
)

NOW = datetime(2026, 9, 26, 10, 0, 0, tzinfo=UTC)


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
            for statement in WORKER_TENANT_CLEANUP:
                await session.execute(text(statement), {"t": tenant.tenant_id})
            await session.commit()

    await cleanup()
    try:
        yield tenant
    finally:
        await cleanup()


def _headers(tenant: TenantContext) -> dict[str, str]:
    # 未带 X-Tenant-Id 时 `get_tenant_id` 会回落 default 租户，故每次请求显式声明。
    return {"X-Tenant-Id": tenant.tenant_id}


def _local(value: datetime) -> str:
    """独立复算期望值：存的是绝对时刻，出参是本地 wall-clock（RULE-time-001）。"""
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


async def _overview(http_client: AsyncClient, tenant: TenantContext) -> dict[str, object]:
    response = await http_client.get("/api/v1/overview", headers=_headers(tenant))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == "0", body
    return body  # type: ignore[no-any-return]


async def test_e01_modules_without_data_yield_zero_and_empty_lists(
    client: AsyncClient, overview_tenant: TenantContext
) -> None:
    """[E-01][integration] 四类数据全空的租户：KPI 全 0、列表全空，整体仍是成功响应。"""
    body = await _overview(client, overview_tenant)
    data = body["data"]
    assert set(data["kpis"]) == KPI_KEYS  # type: ignore[index]
    assert data["kpis"] == {  # type: ignore[index]
        "enabled_agents": 0,
        "enabled_skills": 0,
        "active_tasks": 0,
        "active_schedules": 0,
    }
    assert data["recent_tasks"] == []  # type: ignore[index]
    assert data["next_schedules"] == []  # type: ignore[index]


async def test_e01_partial_data_only_affects_its_own_kpi(
    client: AsyncClient, overview_tenant: TenantContext
) -> None:
    """[E-01][integration] 只有部分模块有数据时，各 KPI 独立计数；停用/软删/非 ACTIVE 不计入。"""
    agent_id = await _seed_agent(overview_tenant, name="启用 Agent")
    await _seed_agent(overview_tenant, name="停用 Agent", enabled=False)
    await _seed_agent(overview_tenant, name="软删 Agent", is_deleted=True)
    await _seed_skill(overview_tenant, enabled=True)
    await _seed_skill(overview_tenant, enabled=False)
    user_id = await _seed_platform_user(overview_tenant, display_name="张三")

    # 非终态 Task 计入；终态与非 ACTIVE/无 next_fire_at 的 Schedule 均不计入。
    await _seed_task(overview_tenant, agent_id=agent_id, actor_user_id=user_id, status="RUNNING")
    await _seed_task(overview_tenant, agent_id=agent_id, actor_user_id=user_id, status="SUCCEEDED")
    await _seed_schedule(
        overview_tenant,
        agent_id=agent_id,
        actor_user_id=user_id,
        name="启用定时",
        next_fire_at=NOW + timedelta(hours=1),
    )
    await _seed_schedule(
        overview_tenant, agent_id=agent_id, actor_user_id=user_id, name="暂停定时", status="PAUSED"
    )
    await _seed_schedule(
        overview_tenant, agent_id=agent_id, actor_user_id=user_id, name="无下次触发"
    )

    data = (await _overview(client, overview_tenant))["data"]
    assert data["kpis"] == {  # type: ignore[index]
        "enabled_agents": 1,
        "enabled_skills": 1,
        "active_tasks": 1,
        # 两个 ACTIVE Schedule 都计入 KPI（设计 §3.2.1 只按 status='ACTIVE' 计数），
        # 其中一个没有 next_fire_at —— 该条件只约束下面的列表。
        "active_schedules": 2,
    }
    # 最近任务只取本次租户的数据；**不过滤状态**（设计 §3.2.1 只按 create_time DESC LIMIT 5），
    # 终态只在 KPI 的 active_tasks 里被排除。
    assert sorted(item["status"] for item in data["recent_tasks"]) == [  # type: ignore[index]
        "RUNNING",
        "SUCCEEDED",
    ]
    # 列表额外要求 next_fire_at IS NOT NULL（设计 §3.2.1），故「无下次触发」不在其中
    assert [item["name"] for item in data["next_schedules"]] == ["启用定时"]  # type: ignore[index]


async def test_rule_api_001_envelope_and_unauthenticated_error() -> None:
    """[RULE-api-001][integration] 统一封套；未认证时按 catalog 的 UNAUTHORIZED 返回。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anonymous:
        denied = await anonymous.get("/api/v1/overview")
    assert denied.status_code == 401
    denied_body = denied.json()
    assert set(denied_body) == ENVELOPE_KEYS
    assert denied_body["code"] == "UNAUTHORIZED"
    assert denied_body["msg"]  # 文案来自 catalog，不得为空


async def test_rule_api_001_contract_and_rule_time_001_time_format(
    client: AsyncClient, overview_tenant: TenantContext
) -> None:
    """[RULE-api-001][RULE-time-001] 冻结契约字段集 + 时间出参 `YYYY-MM-DD HH:mm:ss`。"""
    agent_id = await _seed_agent(overview_tenant, name="策略检查助手")
    user_id = await _seed_platform_user(overview_tenant, display_name="张三")
    older = NOW - timedelta(hours=2)
    await _seed_task(
        overview_tenant, agent_id=agent_id, actor_user_id=user_id, status="RUNNING", create_time=older
    )
    await _seed_task(
        overview_tenant, agent_id=agent_id, actor_user_id=user_id, status="QUEUED", create_time=NOW
    )
    await _seed_schedule(
        overview_tenant,
        agent_id=agent_id,
        actor_user_id=user_id,
        name="每周策略检查",
        next_fire_at=NOW + timedelta(days=3),
    )

    body = await _overview(client, overview_tenant)
    assert set(body) == ENVELOPE_KEYS
    data = body["data"]
    assert set(data) == {"kpis", "recent_tasks", "next_schedules"}  # type: ignore[index]

    tasks = data["recent_tasks"]  # type: ignore[index]
    assert len(tasks) == 2
    # create_time DESC，且出参是本地 wall-clock 的 YYYY-MM-DD HH:mm:ss（RULE-time-001）
    assert [item["create_time"] for item in tasks] == [_local(NOW), _local(older)]
    for item in tasks:
        assert set(item) == TASK_ITEM_KEYS, item
        assert item["agent_name"] == "策略检查助手"
        assert item["actor_user_name"] == "张三"
        for field in ("started_at", "deadline_at", "create_time"):
            assert CONSOLE_TIME.match(item[field]), (field, item[field])
        assert item["finished_at"] is None

    schedule = data["next_schedules"][0]  # type: ignore[index]
    assert set(schedule) == SCHEDULE_ITEM_KEYS, schedule
    assert schedule["name"] == "每周策略检查"
    assert schedule["agent_name"] == "策略检查助手"
    assert schedule["actor_user_name"] == "张三"
    assert schedule["timezone"] == "Asia/Shanghai"
    assert schedule["next_fire_at"] == _local(NOW + timedelta(days=3))
    assert schedule["last_fire_at"] is None


DOCS_07 = Path(__file__).resolve().parents[2] / "docs" / "07-跨模块接口与协议详细设计.md"


def _docs_07_overview_section() -> str:
    text = DOCS_07.read_text(encoding="utf-8")
    match = re.search(r"^### 10\.12 Overview$(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert match, "docs/07 缺 §10.12 Overview 小节"
    return match.group(1)


def test_b201_docs_07_registers_frozen_overview_contract() -> None:
    """[B-201][integration] 跨模块文档登记端点并逐项列出冻结契约字段与错误码（RISK-02）。

    与 `test_rule_api_001_contract_and_rule_time_001_time_format` 形成闭环：后者断言真实响应
    的字段集等于本文件常量，本用例断言 docs/07 §10.12 列出**同一集合** —— 任一侧漂移即失败，
    从而避免契约只活在单个需求的设计文档里。
    """
    section = _docs_07_overview_section()
    assert "GET /api/v1/overview" in section
    for key in sorted(KPI_KEYS):
        assert key in section, f"docs/07 §10.12 缺 KPI 字段 {key}"
    for field in sorted(TASK_ITEM_KEYS | SCHEDULE_ITEM_KEYS):
        assert field in section, f"docs/07 §10.12 缺响应字段 {field}"
    for code in ("UNAUTHORIZED", "COMMON_INTERNAL_ERROR"):
        assert code in section, f"docs/07 §10.12 缺错误码 {code}"
    assert "LIMIT 5" in section, "docs/07 §10.12 须写明两组列表各 LIMIT 5"
    assert "tenant_id" in section, "docs/07 §10.12 须写明按 tenant_id 隔离且只读"
