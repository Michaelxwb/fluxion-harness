"""B-129：前端 Task/Schedule services 与强类型 DTO 契约（设计 §3.5）。

服务层必须经共享 apiClient（自动 X-Locale/X-Request-Id），参数/响应与 Console
HTTP 契约一致，page_size 不超过 100；组件不得裸用 axios/fetch。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/task-schedule"
TASKS = MODULE / "services/tasks.ts"
SCHEDULES = MODULE / "services/schedules.ts"
API_CLIENT = ROOT / "apps/console-platform/frontend/src/api/client.ts"

REQUIRED_METHODS = (
    "listTasks",
    "getTask",
    "cancelTask",
    "listSchedules",
    "getSchedule",
    "pauseSchedule",
    "resumeSchedule",
    "deleteSchedule",
    "listScheduleTasks",
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_task_schedule_services_declare_all_methods() -> None:
    """设计 §3.5 的九类服务方法齐备。"""
    combined = _source(TASKS) + _source(SCHEDULES)
    for method in REQUIRED_METHODS:
        assert f"export async function {method}" in combined, method


def test_services_use_shared_api_client_only() -> None:
    """[RULE-front-001] 只经共享 apiClient；不裸用 axios/fetch，也不自建实例。"""
    for path in (TASKS, SCHEDULES):
        source = _source(path)
        assert "from '../../../api/client'" in source
        assert "api.get" in source
        assert "axios" not in source
        assert "fetch(" not in source
        assert "create(" not in source


def test_services_share_locale_and_request_id_headers() -> None:
    """X-Locale 由共享拦截器发送；写操作显式带 X-Request-Id。"""
    client_source = _source(API_CLIENT)
    assert "config.headers['X-Locale'] = currentLocale()" in client_source
    assert "config.headers['X-Request-Id'] = newRequestId()" in client_source
    for path in (TASKS, SCHEDULES):
        source = _source(path)
        assert "newRequestId" in source


def test_page_size_is_clamped_to_100() -> None:
    tasks_source = _source(TASKS)
    schedules_source = _source(SCHEDULES)
    assert "TASK_PAGE_SIZE_MAX = 100" in tasks_source
    assert "SCHEDULE_PAGE_SIZE_MAX = 100" in schedules_source
    assert "Math.min(pageSize, TASK_PAGE_SIZE_MAX)" in tasks_source
    assert "Math.min(pageSize, SCHEDULE_PAGE_SIZE_MAX)" in schedules_source


def test_task_params_cover_deadline_and_filters() -> None:
    source = _source(TASKS)
    for field in (
        "status?: TaskStatus",
        "trigger_type?: TriggerType",
        "start_time?: string",
        "end_time?: string",
        "deadline_from?: string",
        "deadline_to?: string",
        "page?: number",
        "page_size?: number",
        "schedule_id?: string",
    ):
        assert field in source, field


def test_task_dto_covers_timeline_children_error_and_delivery() -> None:
    source = _source(TASKS)
    for field in (
        "export interface TaskTimelineEvent",
        "export interface TaskChild",
        "timeline: TaskTimelineEvent[]",
        "children: TaskChild[]",
        "error_code: string | null",
        "error_message: string | null",
        "delivery_status: DeliveryStatus",
        "delivery_attempts: number",
        "deadline_at: string",
        "execution_snapshot: Record<string, unknown>",
        "snapshot_hash: string",
    ):
        assert field in source, field
    assert "Page<TaskListItem>" in source


def test_schedule_dto_covers_missed_and_skip_reason() -> None:
    source = _source(SCHEDULES)
    assert "'ACTIVE' | 'PAUSED' | 'COMPLETED' | 'MISSED'" in source
    for field in (
        "next_fire_at: string | null",
        "last_fire_at: string | null",
        "completed_at: string | null",
        "last_error_code: string | null",
        "last_error_message: string | null",
        "last_skipped_at: string | null",
        "revision: number",
    ):
        assert field in source, field
    assert "Page<ScheduleListItem>" in source


def test_schedule_tasks_reuse_task_list_with_schedule_filter() -> None:
    source = _source(TASKS)
    assert re.search(r"export async function listScheduleTasks\([\s\S]*?listTasks\(", source)
    assert "schedule_id: scheduleId" in source
