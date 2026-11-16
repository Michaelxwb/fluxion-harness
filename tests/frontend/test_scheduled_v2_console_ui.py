"""TASK-030：Console Schedule/Task 现有页面的 v2 行为契约。

真实边界：真实 esbuild 编译产物（`scheduleCatalog.ts` 的纯映射层）由真实 node 执行，
以及真实页面/组件源码的接线契约。断言覆盖：

- Fire 历史：真实 Fire 与 DST 事件**分开**表示（DST 行不冒充满意 Fire、不带执行）；
- 空/错态与分页：列表与历史都走 `RemoteTable` + `EmptyState`/`ErrorState`；
- 未知码：目录缺失时展示「未知 (原值)」，不吞原值、也不把裸 schema key 当文案；
- 权限现状：Console 不引入 ADMIN-only，也没有创建入口；
- 动作：pause/resume/cancel 只经 `POST actions`，取消有 FUTURE_ONLY/ALL_ACTIVE 两档；
- 时间：所有时刻走 `DateTimeText`（带 offset 的 Console 格式）。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend"
MODULE = FRONTEND / "src/modules/task-schedule"
CATALOG = MODULE / "scheduleCatalog.ts"
SCHEDULE_PAGE = MODULE / "SchedulePage.tsx"
SCHEDULE_DETAIL = MODULE / "ScheduleDetailSideSheet.tsx"
HISTORY_TABLE = MODULE / "ScheduleHistoryTable.tsx"
ROW_ACTIONS = MODULE / "ScheduleRowActions.tsx"
CANCEL_MODAL = MODULE / "CancelScheduleModal.tsx"
SCHEDULE_SERVICE = MODULE / "services/schedules.ts"
TASK_PAGE = MODULE / "TaskPage.tsx"
TASK_DETAIL = MODULE / "TaskDetailSideSheet.tsx"
ACTIONS_HOOK = MODULE / "useScheduleActions.ts"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

CSV_TIME = "2026-11-16 09:00:00+08:00"
OLD_TIME = "2026-11-15 09:00:00+08:00"
DST_TIME = "2026-11-15 12:00:00+08:00"

DRIVER = """
import * as catalog from './catalog.mjs';

const t = (key) => key;
const fire = (id, at) => ({
  fire_id: id,
  scheduled_at: at,
  status: 'EXECUTION_CREATED',
  skip_reason: null,
  missed_count: 0,
  dst_resolution: null,
  admission_error_code: null,
  execution_id: 'exec-' + id,
  execution_status: 'COMPLETED'
});
const skipped = {
  ...fire('skip', '__OLD__'),
  status: 'SKIPPED',
  skip_reason: 'OVERLAP',
  missed_count: 2,
  execution_id: null,
  execution_status: null
};
const dst = {
  event_id: 'evt',
  event_type: 'DST_GAP_SKIPPED',
  local_date: '2026-03-08',
  local_time: '02:30',
  timezone: 'Asia/Shanghai',
  occurred_at: '__DST__'
};

const rows = catalog.fireHistoryRows({
  items: [fire('newest', '__CSV__'), skipped],
  dst_events: [dst],
  page: 1,
  page_size: 20,
  total: 2,
  retention: null
});

const out = {
  scheduleStatuses: catalog.SCHEDULE_STATUSES,
  scheduleModes: catalog.SCHEDULE_MODES,
  modeLabels: catalog.SCHEDULE_MODES.map((mode) => catalog.scheduleModeLabel(t, mode)),
  unknownStatus: catalog.scheduleStatusLabel(t, 'SOMETHING_NEW'),
  unknownMode: catalog.scheduleModeLabel(t, 'SOMETHING_NEW'),
  taskStatuses: catalog.TASK_STATUSES,
  cancellable: catalog.CANCELLABLE_TASK_STATUSES,
  v1Waiting: catalog.taskStatusLabel(t, 'WAITING'),
  unknownTaskType: catalog.taskTypeLabel(t, 'FANOUT'),
  fireLabel: catalog.fireStatusLabel(t, 'EXECUTION_CREATED'),
  unknownFire: catalog.fireStatusLabel(t, 'BRAND_NEW'),
  skipLabel: catalog.skipReasonLabel(t, 'OVERLAP'),
  skipNull: catalog.skipReasonLabel(t, null),
  dstResolution: catalog.dstResolutionLabel(t, 'FOLD_FIRST'),
  dstEvent: catalog.dstEventLabel(t, 'DST_GAP_SKIPPED'),
  waitKind: catalog.waitKindLabel(t, 'EXTERNAL_POLL'),
  budget: catalog.budgetSummary({ max_steps: 12, max_tool_calls: 20, max_tokens: 0 }),
  budgetNull: catalog.budgetSummary(null),
  snapshot: catalog.snapshotSummary(t, {
    schema_version: 4,
    hash: 'SECRET-HASH',
    mode: 'AGENTIC',
    allowlist: ['MCP:crm:customer.report'],
    memory_mode: 'READ_ONLY',
    budget: { max_steps: 12 }
  }),
  humanOpen: catalog.humanSummary(t, {
    interaction_id: 'i-1',
    question: 'pick one',
    deadline_at: '__CSV__',
    open: true
  }),
  humanNull: catalog.humanSummary(t, null),
  dstSummary: catalog.dstEventSummary(dst),
  rows: rows.map((row) => ({
    kind: row.kind,
    key: row.key,
    at: row.at,
    fireId: row.kind === 'FIRE' ? row.fire.fire_id : null,
    executionId: row.kind === 'FIRE' ? row.fire.execution_id : null,
    eventId: row.kind === 'DST' ? row.event.event_id : null
  }))
};

console.log(JSON.stringify(out));
"""


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _compact(source: str) -> str:
    stripped = re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))
    return re.sub(r"\s+", "", stripped)


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行前端编译产物）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


def _compile_catalog(outdir: Path) -> tuple[Path, Path]:
    bundle = outdir / "catalog.mjs"
    completed = subprocess.run(
        [
            str(ESBUILD),
            str(CATALOG),
            "--bundle",
            "--format=esm",
            "--platform=node",
            f"--outfile={bundle}",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"编译失败 {CATALOG.name}：{completed.stderr}"
    driver = outdir / "driver.mjs"
    driver.write_text(
        DRIVER.replace("__CSV__", CSV_TIME).replace("__OLD__", OLD_TIME).replace("__DST__", DST_TIME),
        encoding="utf-8",
    )
    return bundle, driver


@pytest.fixture(scope="module")
def catalog_facts(tmp_path_factory: pytest.TempPathFactory) -> dict:
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    outdir = tmp_path_factory.mktemp("schedv2-console-ui")
    _compile_catalog(outdir)
    completed = subprocess.run(
        [str(NODE), str(outdir / "driver.mjs")],
        capture_output=True,
        text=True,
        cwd=str(outdir),
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_catalog_maps_every_v2_schedule_status_and_mode(catalog_facts: dict) -> None:
    """五种状态与三种模式都有词条；不再出现 v1 的 COMPLETED/MISSED 状态。"""
    assert catalog_facts["scheduleStatuses"] == [
        "ACTIVE",
        "PAUSED",
        "NEEDS_ATTENTION",
        "ENDED",
        "CANCELLED",
    ]
    assert catalog_facts["scheduleModes"] == ["REMINDER", "SKILL", "AGENTIC"]
    assert catalog_facts["modeLabels"] == [
        "schedule.mode.REMINDER",
        "schedule.mode.SKILL",
        "schedule.mode.AGENTIC",
    ]


def test_unknown_codes_keep_the_raw_value_readable(catalog_facts: dict) -> None:
    """后端新增枚举而前端未跟上时：通用文案 + 原值，不静默变空白也不露裸 key。"""
    assert catalog_facts["unknownStatus"] == "schedule.status.unknown (SOMETHING_NEW)"
    assert catalog_facts["unknownMode"] == "schedule.mode.unknown (SOMETHING_NEW)"
    assert catalog_facts["v1Waiting"] == "task.status.unknown (WAITING)"
    assert catalog_facts["unknownTaskType"] == "task.taskType.unknown (FANOUT)"
    assert catalog_facts["unknownFire"] == "schedule.fire.status.unknown (BRAND_NEW)"


def test_task_waiting_is_split_into_two_distinct_states(catalog_facts: dict) -> None:
    task_statuses = catalog_facts["taskStatuses"]
    assert "WAITING_EXTERNAL" in task_statuses and "WAITING_HUMAN" in task_statuses
    assert "WAITING" not in task_statuses
    assert "FAILED_CONFIG" not in task_statuses
    assert catalog_facts["waitKind"] == "task.waitKind.EXTERNAL_POLL"
    # 两类等待都可取消；终态不可取消。
    assert catalog_facts["cancellable"] == [
        "QUEUED",
        "RUNNING",
        "WAITING_EXTERNAL",
        "WAITING_HUMAN",
    ]


def test_fire_and_dst_events_stay_distinct_facts(catalog_facts: dict) -> None:
    """合并排序但类别不混：Fire 行带执行链接，DST 行只描述本地时刻。"""
    assert catalog_facts["fireLabel"] == "schedule.fire.status.EXECUTION_CREATED"
    assert catalog_facts["skipLabel"] == "schedule.fire.skipReason.OVERLAP"
    assert catalog_facts["skipNull"] == "-"
    assert catalog_facts["dstResolution"] == "schedule.fire.dstResolution.FOLD_FIRST"
    assert catalog_facts["dstEvent"] == "schedule.fire.dstEvent.DST_GAP_SKIPPED"
    assert catalog_facts["dstSummary"] == "2026-03-08 02:30 · Asia/Shanghai"

    rows = catalog_facts["rows"]
    assert [row["kind"] for row in rows] == ["FIRE", "DST", "FIRE"], "按触发时刻倒序合并"
    assert [row["key"] for row in rows] == ["fire-newest", "dst-evt", "fire-skip"]
    dst_row = rows[1]
    assert dst_row["fireId"] is None and dst_row["executionId"] is None
    assert dst_row["eventId"] == "evt"
    assert rows[0]["executionId"] == "exec-newest"
    assert rows[2]["executionId"] is None, "跳过的 occurrence 没有执行"


def test_snapshot_and_human_summaries_render_backend_whitelist(catalog_facts: dict) -> None:
    snapshot = catalog_facts["snapshot"]
    for token in (
        "task.detail.snapshotSchema=4",
        "task.detail.snapshotMode=AGENTIC",
        "task.detail.memoryMode=READ_ONLY",
        "MCP:crm:customer.report",
        'task.detail.budget={"max_steps":12}',
    ):
        assert token in snapshot, token
    assert "SECRET-HASH" not in snapshot, "快照摘要不渲染 hash 之外未列出的字段"
    assert catalog_facts["humanOpen"].startswith("task.detail.humanQuestion: pick one")
    assert "task.detail.humanOpen" in catalog_facts["humanOpen"]
    assert catalog_facts["humanNull"] == "-"
    assert catalog_facts["budget"] == "steps=12 · tools=20 · tokens=0"
    assert catalog_facts["budgetNull"] == "-"


def test_schedule_list_has_no_create_entry_and_keeps_deep_link() -> None:
    """Console 无创建入口（E-12）；深链 `?scheduleId=` 仍打开详情。"""
    page = _read(SCHEDULE_PAGE)
    assert "searchParams.get('scheduleId')" in page
    for forbidden in ("createSchedule", "CreateSchedule", "schedule-create", "POST /schedules"):
        assert forbidden not in page, f"不得出现创建入口：{forbidden}"
    # 只有帮助弹窗；没有表单弹窗。
    assert page.count("<Modal") == 1
    assert "Form" not in page
    assert "require_admin" not in page and "role ===" not in page, "权限保持现状，不引入 ADMIN-only"


def test_schedule_list_renders_v2_facts_with_empty_and_error_states() -> None:
    page = _read(SCHEDULE_PAGE)
    for token in (
        "schedule.columns.mode",
        "schedule.columns.user",
        "schedule.columns.attention",
        "schedule.columns.nextFireAt",
        "schedule.columns.lastFireAt",
        "schedule.columns.status",
        "schedule.filter.agent",
        "schedule.filter.user",
        "schedule-filter-agent",
        "schedule-filter-user",
        "listAgentsForPicker",
        "listUsers",
        "actor_user_id",
        "actor_name",
        "actionColumn(130)",
        "columnGrowth",
        "schedule.badge.pendingHuman",
        "schedule.badge.needsAttention",
        "setParams((prev) => ({ ...prev, page }))",
        "onPageSizeChange",
        "<ErrorState",
        "<EmptyState",
        "<DateTimeText",
    ):
        assert token in page, token
    assert "SCHEDULE_STATUSES" in page, "状态筛选选项来自 catalog 的 v2 集合"


def test_schedule_actions_only_use_the_post_actions_service() -> None:
    hook = _read(ACTIONS_HOOK)
    assert "runScheduleAction" in hook and "deleteSchedule" in hook
    assert "pauseSchedule" not in hook and "resumeSchedule" not in hook
    assert "scope" in hook and "CancelScope" in hook

    service = _read(SCHEDULE_SERVICE)
    assert "api.put" not in service, "旧 PUT pause/resume 已移除"

    rows = _read(ROW_ACTIONS)
    for testid in ("schedule-pause-", "schedule-resume-", "schedule-cancel-", "schedule-delete-"):
        assert testid in rows, testid
    assert "CancelScheduleModal" in rows
    assert "admission_error" in rows, "恢复时准入失败要显式给出修复指引"

    modal = _read(CANCEL_MODAL)
    assert "DEFAULT_SCOPE: CancelScope = 'FUTURE_ONLY'" in modal, "默认只取消未来触发"
    assert "data-testid={`schedule-cancel-scope-${option}`}" in modal
    assert "schedule.cancel.scope.${option}" in modal


def test_schedule_detail_shows_revision_dependencies_delivery_budget_and_end_reason() -> None:
    detail = _read(SCHEDULE_DETAIL)
    for token in (
        "schedule.detail.revision",
        "schedule.detail.configVersion",
        "schedule.detail.dependencies",
        "schedule.detail.deliveryIntent",
        "schedule.detail.budget",
        "schedule.detail.memory",
        "schedule.detail.attentionReason",
        "detail.end_reason",
        "detail.dependencies",
        "detail.delivery_intent",
        "detail.budget",
        "detail.memory_mode",
        "budgetSummary",
        "memoryModeLabel",
        "schedule-detail-end-reason",
        "endReasonLabel",
    ):
        assert token in detail, token
    assert "intent_key" not in detail
    assert "schedule.columns.cron" not in detail, "v2 详情不展示已退役的 cron 列"
    assert "navigate(`/tasks?scheduleId=${detail.schedule_id}`)" in detail, "深链保留"


def test_fire_history_table_paginates_and_separates_rows() -> None:
    table = _read(HISTORY_TABLE)
    assert "listScheduleFires" in table
    assert "fireHistoryRows" in table
    assert "dst_events" not in table, "页面只消费合并后的行；分开的事实由 catalog 保证"
    for token in (
        "onPageChange={setPage}",
        "onPageSizeChange",
        "<ErrorState",
        "<EmptyState",
        "fire-execution-",
        "dst-event-",
    ):
        assert token in table, token
    assert "<DateTimeText" in table


def test_task_pages_show_v2_status_wait_snapshot_and_human() -> None:
    page = _read(TASK_PAGE)
    assert "TASK_STATUSES" in page and "CANCELLABLE_TASK_STATUSES" in page
    assert "task.columns.taskType" in page
    for token in (
        "task-filter-search",
        "onSearchChange",
        "task.filter.searchTaskId",
        "CompactDateRange",
        "triggerRender",
        "task.filter.created",
        "task.filter.deadline",
        "resetFilters",
    ):
        assert token in page, token
    for retired in ("task.columns.deliveryStatus", "delivery_status", "intent_key"):
        assert retired not in page, retired

    detail = _read(TASK_DETAIL)
    for token in (
        "task.detail.waitKind",
        "detail.wait_kind",
        "task.detail.human",
        "detail.human",
        "task.detail.snapshot",
        "detail.snapshot",
        "snapshotSummary",
        "humanSummary",
        "taskTypeLabel",
    ):
        assert token in detail, token
    for retired in ("execution_snapshot", "delivery_status", "intent_key"):
        assert retired not in detail, retired
    assert "WAITING_EXTERNAL" in detail, "wait_kind 只在外部等待时展示"


def test_pages_never_format_times_inline() -> None:
    """时间一律走 DateTimeText（本地时区 + Console 格式），页面不自行格式化。"""
    for path in (SCHEDULE_PAGE, SCHEDULE_DETAIL, HISTORY_TABLE, TASK_PAGE, TASK_DETAIL):
        source = _read(path)
        assert "toLocaleString(" not in source, path.name
        assert "DateTimeText" in _compact(source), path.name
