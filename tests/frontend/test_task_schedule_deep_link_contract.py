"""Task/Schedule 深链：**发出方 ↔ 接收方**的键名配对契约。

跨页定位统一落到列表页并携带 camelCase 查询参数（口径见 `AuditDetailSideSheet.handleOpenRelated`
的注释）。这类链路的失效方式是**静默**的：发出方改了键名、接收方没跟上，页面照常渲染，
只是打开了一个无关的空列表——`docs/issues/2026-10-03-audit-run-link.md` 就是这么发生的，
而当时的 e2e 只断言 URL 变了、不断言记录打开了。

本契约把两侧的键钉在一起，键名一改就红。

**已知未闭环**：`AuditDetailSideSheet` 还发出 `runId`，但 Console 没有 Run 承载页
（issue 里明确「具体承载页面待设计确认」），故不在下方配对内。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "apps/console-platform/frontend/src"

TASK_PAGE = SRC / "modules/task-schedule/TaskPage.tsx"
SCHEDULE_PAGE = SRC / "modules/task-schedule/SchedulePage.tsx"
TASK_DETAIL = SRC / "modules/task-schedule/TaskDetailSideSheet.tsx"
SCHEDULE_DETAIL = SRC / "modules/task-schedule/ScheduleDetailSideSheet.tsx"
ENTITY_NAME = SRC / "modules/task-schedule/EntityNameText.tsx"
COPYABLE_TEXT = SRC / "components/common/CopyableText.tsx"
AUDIT_DETAIL = SRC / "modules/audit-observability/components/AuditDetailSideSheet.tsx"

#: (发出方文件, 发出的字面量, 目标页, 接收方必须读取的键)
#: 概览页曾是深链发出方（列表条目 → /tasks?taskId=）；v2 指标化改造移除列表后不再发出，
#: 深链入口收敛到运行审计详情与定时任务详情。
DEEP_LINKS = (
    (SCHEDULE_DETAIL, "/tasks?scheduleId=", TASK_PAGE, "scheduleId"),
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_every_emitted_deep_link_is_consumed_by_its_target_page() -> None:
    for producer, literal, consumer, key in DEEP_LINKS:
        emitted = _read(producer)
        assert literal in emitted, f"{producer.name} 不再发出 {literal}（配对本表需同步更新）"
        read_back = _read(consumer)
        assert f"searchParams.get('{key}')" in read_back, (
            f"{consumer.name} 没读取 {key}：{producer.name} 发出的 {literal} 会静默退化成空列表"
        )


def test_audit_relation_task_link_is_consumed() -> None:
    """审计「关联 Task」的键由 `relation` 拼出，静态字面量匹配不到，单独钉。"""
    audit = _read(AUDIT_DETAIL)
    assert "navigate(`/tasks?taskId=${id}`)" in audit
    assert "'run' | 'task'" in audit
    assert "searchParams.get('taskId')" in _read(TASK_PAGE)


def test_deep_linked_schedule_filter_is_clearly_removable() -> None:
    """深链进来的 schedule 过滤必须看得见也去得掉，否则用户被困在一个隐式筛选里。"""
    task_page = _read(TASK_PAGE)
    assert 'data-testid="task-filter-schedule"' in task_page
    assert "setFilter({ schedule_id: undefined })" in task_page


def test_task_detail_links_back_to_schedule_only_for_scheduled_triggers() -> None:
    """反向链接只在定时触发的任务上出现；展示组件不自行导航，由页面接线。"""
    detail = _read(TASK_DETAIL)
    assert "detail.trigger_type === 'SCHEDULED' && detail.schedule_id" in detail
    assert 'testId="task-detail-schedule"' in detail
    assert "onOpenSchedule" in detail
    assert "useNavigate" not in detail, "详情组件只回调，不自行导航"


def test_task_page_mounts_the_schedule_sheet_for_the_reverse_link() -> None:
    task_page = _read(TASK_PAGE)
    assert "<ScheduleDetailSideSheet" in task_page
    assert "onOpenSchedule={(scheduleId) => setDetailScheduleId(scheduleId)}" in task_page


def test_task_filters_are_forwarded_to_the_list_request() -> None:
    """§11.3 要求的 Agent/执行用户/Skill/时间筛选必须真的进查询参数，而不只是画个控件。"""
    task_page = _read(TASK_PAGE)
    for field in ("agent_id", "actor_user_id", "skill_id", "start_time", "end_time"):
        assert f"{field}:" in task_page, field
    for testid in (
        "task-filter-agent",
        "task-filter-actor",
        "task-filter-skill",
        "task-filter-create",
        "task-filter-deadline",
        "task-reset",
    ):
        assert f'data-testid="{testid}"' in task_page, testid


def test_picker_options_are_loaded_lazily_not_on_page_load() -> None:
    """下拉可选项首次展开才取：进页面就抓会多发三个请求，端点不可用时还会连弹错误。"""
    task_page = _read(TASK_PAGE)
    assert "usePickerOptions(" in task_page
    assert "onDropdownVisibleChange" in task_page
    assert "onDropdownVisibleChange={agentPicker.onDropdownVisibleChange}" in task_page
    assert "onDropdownVisibleChange={actorPicker.onDropdownVisibleChange}" in task_page
    assert "onDropdownVisibleChange={skillPicker.onDropdownVisibleChange}" in task_page


def test_entity_columns_prefer_names_with_short_id_fallback() -> None:
    """Agent/执行用户/Skill 三列名称优先、短 id 兜底；完整值进 title 与复制。"""
    for page in (TASK_PAGE, SCHEDULE_PAGE):
        source = _read(page)
        assert "EntityNameText" in source, f"{page.name} 三列须走名称化单元格"
        for field in ("agent_name", "actor_name", "skill_name", "skill_key"):
            assert field in source, f"{page.name} 缺少名称字段 {field}"

    cell = _read(ENTITY_NAME)
    assert "id.slice(0, 8)" in cell, "短 id 口径 = 前 8 位"
    assert "name ?" in cell and "keySuffix" in cell, "名称优先；Skill 名称带 key 后缀"
    copyable = _read(COPYABLE_TEXT)
    assert "copyable" in copyable and "title={full}" in copyable, "title 与复制都取完整值"


def test_task_list_hides_redundant_intent_column() -> None:
    """业务意图与 Skill 列重复：两个列表都隐藏该列（词条仍被任务详情消费，不删除）。"""
    assert "task.columns.intent" not in _read(TASK_PAGE)
    assert "task.columns.intent" in _read(TASK_DETAIL)
    assert "schedule.columns.intent" not in _read(SCHEDULE_PAGE)


def test_child_progress_only_for_batch_rows_with_children() -> None:
    """非 BATCH（或 child_total 为 0）的「子任务进度」显示 `-`，不显示 `0/0`。"""
    task_page = _read(TASK_PAGE)
    assert "task_type === 'BATCH'" in task_page
    assert "child_total ?? 0) > 0" in task_page
    assert "'-'" in task_page
