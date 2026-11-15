"""B-129：前端 Task/Schedule services 与强类型 DTO 契约（scheduled-task-v2 §3.10）。

服务层必须经共享 apiClient（自动 X-Locale/X-Request-Id），参数/响应与 Console HTTP 契约一致，
page_size 不超过 100；组件不得裸用 axios/fetch。v2 的契约事实：

- Schedule 生命周期动作只有 `POST /schedules/{id}/actions`（`pause|resume|cancel`），
  旧 `PUT pause|resume` 已移除，服务层不得保留对应方法或路由；
- Fire 历史与 DST 事件在同一次分页响应里分开返回（`items` + `dst_events`）；
- Task 不再有 `intent_key`/`delivery_*`，等待拆成 `WAITING_EXTERNAL`（含 `wait_kind`）
  与 `WAITING_HUMAN`。
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
    "listScheduleFires",
    "runScheduleAction",
    "deleteSchedule",
)

RETIRED_METHODS = ("pauseSchedule", "resumeSchedule", "listScheduleTasks")


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code(path: Path) -> str:
    """去掉注释后的源码：退役字段只允许出现在历史说明里。"""
    source = _source(path)
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def test_task_schedule_services_declare_all_methods() -> None:
    """设计 §3.10 的服务面齐备（v2 只有 actions 一个动作入口）。"""
    combined = _source(TASKS) + _source(SCHEDULES)
    for method in REQUIRED_METHODS:
        assert f"export async function {method}" in combined, method
    for retired in RETIRED_METHODS:
        assert f"export async function {retired}" not in combined, retired


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


def test_schedule_writes_carry_the_idempotency_key() -> None:
    """写操作带幂等键：网络重试沿用同一键由服务端重放首次结果。"""
    source = _source(SCHEDULES)
    assert "Idempotency-Key" in source
    assert "'X-Request-Id': newRequestId(), 'Idempotency-Key': newRequestId()" in source


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


def test_task_dto_covers_timeline_children_error_and_v2_waiting() -> None:
    """Task 详情：时间线/子任务/错误 + 白名单快照 + Human 摘要 + `wait_kind`。"""
    source = _source(TASKS)
    for field in (
        "export interface TaskTimelineEvent",
        "export interface TaskChild",
        "export interface TaskSnapshotSummary",
        "export interface TaskHumanSummary",
        "timeline: TaskTimelineEvent[]",
        "children: TaskChild[]",
        "snapshot: TaskSnapshotSummary",
        "human: TaskHumanSummary | null",
        "wait_kind: WaitKind | null",
        "error_code: string | null",
        "error_message: string | null",
        "deadline_at: string",
    ):
        assert field in source, field
    assert "Page<TaskListItem>" in source


def test_task_statuses_are_the_v2_set_without_delivery_columns() -> None:
    """`WAITING` 拆成两类等待；`FAILED_CONFIG`/`delivery_*`/`intent_key` 已退役。"""
    source = _code(TASKS)
    assert "'WAITING_EXTERNAL'" in source and "'WAITING_HUMAN'" in source
    assert "'WAITING'" not in source.replace("'WAITING_EXTERNAL'", "").replace(
        "'WAITING_HUMAN'", ""
    )
    for retired in ("FAILED_CONFIG", "delivery_status", "delivery_mode", "delivered_at", "intent_key"):
        assert retired not in source, retired
    for wait_kind in ("ASYNC_OPERATION", "EXTERNAL_POLL", "BATCH_FANIN"):
        assert wait_kind in source, wait_kind


def test_task_and_schedule_dtos_declare_resolved_names() -> None:
    """列表接口注入名称字段（Console control.* 权威源）；缺失为 null，前端回落短 id。"""
    task_source = _source(TASKS)
    for field in (
        "agent_name?: string | null",
        "actor_name?: string | null",
        "skill_name?: string | null",
        "skill_key?: string | null",
    ):
        assert field in task_source, f"tasks.ts 缺少 {field}"
    # v2 的 Schedule 列表项只有 agent_id：执行用户与 Skill 不在它的投影里，不声明假字段。
    schedule_source = _source(SCHEDULES)
    assert "agent_name?: string | null" in schedule_source
    for absent in ("actor_name?: string | null", "skill_name?: string | null"):
        assert absent not in schedule_source, f"schedules.ts 声明了后端不返回的字段 {absent}"


def test_schedule_dto_covers_v2_status_mode_and_revision() -> None:
    source = _source(SCHEDULES)
    assert "'ACTIVE' | 'PAUSED' | 'NEEDS_ATTENTION' | 'ENDED' | 'CANCELLED'" in source
    for field in (
        "mode: ScheduleMode",
        "timezone: string",
        "config_version: number",
        "next_fire_at: string | null",
        "last_fire: LastFireSummary | null",
        "needs_attention: boolean",
        "pending_human: boolean",
        "revision_no: number",
        "dependencies: string[]",
        "delivery_intent: DeliveryIntent",
        "budget: AgenticBudget | null",
        "memory_mode: MemoryMode | null",
        "end_reason: ScheduleEndReason | null",
    ):
        assert field in source, field
    for retired in ("completed_at", "last_error_code", "last_skipped_at", "cron_expr"):
        assert retired not in source, retired
    assert "Page<ScheduleListItem>" in source


def test_fire_history_separates_dst_events_from_runs() -> None:
    """Fire 历史是 `{items, dst_events, page, page_size, total}`；DST 事件不是 Fire。"""
    source = _source(SCHEDULES)
    assert "export interface FireItem" in source
    assert "export interface DstEvent" in source
    assert "export interface FireHistoryPage" in source
    assert "dst_events: DstEvent[]" in source
    assert "items: FireItem[]" in source
    for field in (
        "skip_reason",
        "missed_count",
        "dst_resolution",
        "admission_error_code",
        "execution_status",
    ):
        assert field in source, field


def test_schedule_actions_use_the_single_post_route() -> None:
    """唯一动作入口是 `POST /schedules/{id}/actions`；旧 `PUT pause|resume` 不保留。"""
    source = _source(SCHEDULES)
    assert "api.post<ApiResponse<ScheduleActionResult>>(`/schedules/${id}/actions`" in source
    assert "api.put" not in source
    assert re.search(r"`/schedules/\$\{id\}/(pause|resume)`", source) is None
    for action in ("pause", "resume", "cancel"):
        assert f"'{action}'" in source, action
    assert "CancelScope" in source and "FUTURE_ONLY" in source and "ALL_ACTIVE" in source


def test_schedule_tasks_reuse_task_list_with_schedule_filter() -> None:
    """深链 `?scheduleId=` 仍落到 Task 列表的 `schedule_id` 过滤（服务层无重复包装）。"""
    source = _source(TASKS)
    assert "schedule_id?: string" in source
    assert "params: normalizeParams(params)" in source
