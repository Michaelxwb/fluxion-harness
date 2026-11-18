"""Schedule 的查询与管理员清理（B-110 / RULE-api-001）——v2 口径。

**迁移说明（scheduled-task-v2 TASK-033）**：v1 的创建/修改/动作路由已在 TASK-013 退役，
本文件原先的十个用例按事实重新落位：

| 原用例 | 现在的归属 |
|---|---|
| 列表分页封套 / 状态筛选 / agent 筛选 / 分页上限 | `test_scheduled_v2_read_api.py`（TASK-023 读面） |
| 详情与租户隔离 | 同上（`test_read_surface_is_tenant_and_owner_scoped`） |
| 重复暂停幂等 / 终态拒绝暂停恢复 / 恢复重算指针 | `test_scheduled_v2_lifecycle.py`（TASK-013 状态矩阵） |
| 重复删除幂等 / 删除不影响既有任务 | **本文件**（软删只保留管理员清理入口） |

用户面不再有删除入口：v2 的历史保留策略是软删只用于管理员清理（design §3.4.5），
因此这两条事实落在 `/internal/admin/schedules/{id}` 上。

真实边界：真实 ASGI HTTP → ScheduleService → 真实 PostgreSQL。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from muad_agent_worker.infrastructure.models.task import TaskSchedule, TaskScheduleRevision

from agent_worker.conftest import TenantContext
from agent_worker.helpers import internal_service_headers, persist_task

OWNER = uuid.UUID("5a1d0c1e-0000-4000-8000-00000000a001")


def _headers(tenant: TenantContext) -> dict[str, str]:
    return internal_service_headers(tenant.tenant_id, **{"X-Actor-User-Id": str(OWNER)})


async def _seed_schedule(tenant: TenantContext) -> uuid.UUID:
    """直插 v2 的 Schedule + Revision（创建入口已是 Draft + 确认）。"""
    schedule_id, revision_id = uuid.uuid4(), uuid.uuid4()
    async with tenant.session_factory() as session, session.begin():
        schedule = TaskSchedule(
            id=schedule_id,
            tenant_id=tenant.tenant_id,
            owner_user_id=OWNER,
            agent_id=uuid.uuid4(),
            name="清理用例任务",
            status="ACTIVE",
            config_version=1,
            next_fire_at=datetime.now(UTC) + timedelta(hours=1),
            created_by=OWNER,
        )
        session.add(schedule)
        await session.flush()
        session.add(
            TaskScheduleRevision(
                id=revision_id,
                tenant_id=tenant.tenant_id,
                schedule_id=schedule_id,
                revision_no=1,
                mode="REMINDER",
                instruction_raw="提醒我开会",
                normalized_goal="提醒开会",
                recurrence_kind="CRON",
                cron_expr="0 8 * * *",
                timezone="UTC",
                schedule_policy={},
                context_json={},
                payload_kind="REMINDER", payload_json={"kind": "REMINDER", "text": "开会"},
                delivery_intent="NONE",
                preflight_json={},
                created_by=OWNER,
            )
        )
        await session.flush()
        schedule.current_revision_id = revision_id
    return schedule_id


@pytest.mark.asyncio
async def test_admin_delete_is_idempotent(client: AsyncClient, tenant: TenantContext) -> None:
    """已删除再次删除幂等返回成功。"""
    schedule_id = await _seed_schedule(tenant)

    first = await client.delete(f"/internal/admin/schedules/{schedule_id}", headers=_headers(tenant))
    second = await client.delete(
        f"/internal/admin/schedules/{schedule_id}", headers=_headers(tenant)
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, f"重复删除应幂等，得到 {second.status_code}"


@pytest.mark.asyncio
async def test_admin_delete_does_not_remove_existing_tasks(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """软删 Schedule 不影响它已经产生的执行（历史保留）。"""
    schedule_id = await _seed_schedule(tenant)
    task = await persist_task(tenant, schedule_id=schedule_id, actor_user_id=OWNER)

    deleted = await client.delete(
        f"/internal/admin/schedules/{schedule_id}", headers=_headers(tenant)
    )
    assert deleted.status_code == 200, deleted.text

    detail = await client.get(f"/internal/tasks/{task.id}", headers=_headers(tenant))
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["task_id"] == str(task.id)
