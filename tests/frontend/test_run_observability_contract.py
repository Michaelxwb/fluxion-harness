"""Run 只读视图的源码契约（形态 A：嵌套 SideSheet，不新增导航页；TASK-008 扩等待/操作投影）。

约束在这里机检，避免只靠约定：

1. **不新增页面/导航项**——Run 用嵌套 SideSheet 承载，`menu.ts` 不出现 Run 条目；
2. **不暴露内容**——前端不引用对话原文/事件负载/结果字段（后端本来也不返回，两侧同向）；
3. **不再"统一落到任务列表"**——Run 关联就地打开（`RelatedDetailController`），Task 关联才跳列表；
4. **等待/操作投影**——RunStatus/OperationStatus 与 contracts 枚举逐字对齐，分页是封套 `Page`；
5. **请求卫生**——组件不裸 HTTP；过期响应按 requestSeq 失效；Task 未受理不造链接；
6. **i18n**——动态键（状态/模式/错误阶段/事件）两侧枚举齐全，译文不在模块态缓存。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "apps/console-platform/frontend/src"
MODULE = SRC / "modules/run-observability"
RUN_SERVICE = MODULE / "services/runs.ts"
RUN_SHEET = MODULE / "RunDetailSideSheet.tsx"
RUN_TABLE = MODULE / "components/RunOperationTable.tsx"
RUN_TIMELINE = MODULE / "components/RunTimelineOutline.tsx"
STATUS_OPTIONS = MODULE / "statusOptions.ts"
CONTROLLER = MODULE / "RelatedDetailController.tsx"
HOOK_DETAIL = MODULE / "hooks/useRunDetail.ts"
HOOK_OPERATIONS = MODULE / "hooks/useRunOperations.ts"
AUDIT_DETAIL = SRC / "modules/audit-observability/components/AuditDetailSideSheet.tsx"
TASK_DETAIL = SRC / "modules/task-schedule/TaskDetailSideSheet.tsx"
TASK_PAGE = SRC / "modules/task-schedule/TaskPage.tsx"
MENU = SRC / "config/menu.ts"
BACKEND_ENUMS = ROOT / "packages/contracts/src/muad_contracts/enums.py"
LOCALES = ("zh-CN", "en-US")

# 设计 §3.5 I18N-01 点名必须枚举的新事件类型（其余已展示事件由 RUN_EVENT_TYPES 数组承载）
REQUIRED_EVENT_TYPES = {
    "TOOL_SUBMISSION_PENDING",
    "TOOL_TASK_ACCEPTED",
    "TOOL_RESULT_RECEIVED",
    "BACKGROUND_RESULT",
    "BACKGROUND_RESULT_LATE",
    "RUN_WAITING_TOOL",
    "RUN_RESUMED",
}

# 模块作用域或组件状态里缓存译文（语言切换后不再更新）
CACHED_TRANSLATION = re.compile(
    r"^const\s+\w+\s*(?::[^=]+)?=\s*t\("
    r"|use(?:State|Memo|Ref)(?:<[^>]*>)?\(\s*(?:\(\)\s*=>\s*)?t\(",
    re.MULTILINE,
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _module_sources() -> list[Path]:
    """模块内全部 `.ts/.tsx`：新增文件自动纳入扫描。"""
    return sorted(path for path in MODULE.rglob("*") if path.suffix in {".ts", ".tsx"})


def _locale(name: str) -> dict[str, str]:
    payload = json.loads(_read(SRC / f"locales/{name}.json"))
    assert all(isinstance(value, str) for value in payload.values()), "词条须是扁平 JSON"
    return payload


def _frontend_union(source: str, name: str) -> set[str]:
    match = re.search(rf"export type {name} =\s*([^;]+);", source)
    assert match, f"缺少导出联合类型 {name}"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def _backend_enum(name: str) -> set[str]:
    match = re.search(
        rf"class {name}\(StrEnum\):\n((?:    [A-Z_]+ = \"[^\"]+\"\n)+)", _read(BACKEND_ENUMS)
    )
    assert match, f"contracts 缺少枚举 {name}"
    return set(re.findall(r"= \"([^\"]+)\"", match.group(1)))


def _string_array(source: str, name: str) -> tuple[str, ...]:
    match = re.search(rf"(?:export\s+)?const\s+{name}\s*=\s*\[(.*?)\]\s*as const;", source, re.DOTALL)
    assert match, f"缺少枚举数组声明 {name}"
    return tuple(re.findall(r"'([^']+)'", match.group(1)))


def test_run_view_does_not_add_a_navigation_entry() -> None:
    """形态 A 的前提：Run 不开独立页面。开了页面就得同时改导航与 sidebar e2e 的计数。"""
    menu = _read(MENU)
    assert "/runs" not in menu
    assert "nav.run" not in menu


def test_run_service_uses_the_shared_api_client() -> None:
    source = _read(RUN_SERVICE)
    assert "from '../../../api/client'" in source
    assert "api.get" in source
    assert "axios" not in source
    assert "fetch(" not in source
    assert "export async function getRun" in source
    assert "export async function listRunOperations" in source


def test_run_view_never_reads_conversation_content() -> None:
    """Console 至今零暴露对话原文；这条把「不回退」钉住，而不是靠口头约定。

    检查的是前端**有没有给这些字段起名**（DTO 键或渲染引用）。源码注释里刻意不写它们的
    字面量，否则解释「为什么不给」的那句话自己就会把用例打红。
    """
    for path in _module_sources():
        source = _read(path)
        for token in ("input_text", "inputText", "payload", "payload_json", "result_json"):
            assert token not in source, f"{path}: {token}"


def test_audit_run_relation_opens_in_place_rather_than_jumping_to_tasks() -> None:
    audit = _read(AUDIT_DETAIL)
    compact = _compact(audit)
    assert "related.openRun(id)" in compact, "Run 关联就地打开，不跳页"
    assert "navigate(`/tasks?taskId=${id}`)" in compact
    # Run 不再走"统一落到任务列表"那条路
    assert "navigate(`/tasks?${relation}Id=" not in compact
    assert "<RelatedDetailController" in audit
    assert "useRelatedDetail" in audit


def test_task_detail_links_back_to_its_source_run() -> None:
    detail = _read(TASK_DETAIL)
    assert "detail.source_run_id" in detail
    assert 'testId="task-detail-run"' in detail
    assert "onOpenRun" in detail
    assert "useNavigate" not in detail, "详情组件只回调，不自行导航"
    page = _read(TASK_PAGE)
    assert "<RelatedDetailController" in page
    assert "onOpenRun={(runId) => related.openRun(runId)}" in page


def test_run_detail_reports_truncation_instead_of_pretending_completeness() -> None:
    sheet = _read(RUN_SHEET)
    assert "timeline_truncated" in sheet
    timeline = _read(RUN_TIMELINE)
    assert 'data-testid="run-detail-truncated"' in timeline
    assert "truncated" in timeline


def test_run_labels_are_localized_in_both_locales() -> None:
    for locale in LOCALES:
        payload = _locale(locale)
        for status in (
            "CREATED",
            "RUNNING",
            "WAITING_TOOL",
            "WAITING_INPUT",
            "COMPLETED",
            "FAILED",
            "CANCELLED",
        ):
            assert payload[f"run.status.{status}"], (locale, status)
        for key in (
            "run.detail.operations",
            "run.detail.refresh",
            "run.detail.waitingSince",
            "run.detail.deadline",
            "run.detail.pendingJoin",
            "run.detail.pendingSubmission",
            "run.detail.continuations",
            "run.detail.operationsEmpty",
            "run.detail.unavailable",
        ):
            assert payload[key], (locale, key)


def test_run_service_exposes_waiting_projection_and_paged_operations() -> None:
    """API-05/06 的 DTO 形状：等待字段、精确联合、封套分页（默认 15、上限 100）。"""
    source = _read(RUN_SERVICE)
    for field in (
        "waiting_since",
        "deadline_at",
        "waiting_reason",
        "pending_join_count",
        "pending_submission_count",
        "continuation_count",
    ):
        assert field in source, field
    assert "export type WaitReason = 'SUBMISSION' | 'TASK_RESULT' | 'RESUME_READY'" in source
    assert "OPERATIONS_PAGE_SIZE_DEFAULT = 15" in source
    assert "OPERATIONS_PAGE_SIZE_MAX = 100" in source
    assert "Promise<Page<RunOperationOutline>>" in source, "operations 响应必须是封套 Page，不是裸数组"
    assert "Promise<RunOperationOutline[]>" not in source
    assert "export interface RunOperationOutline" in source


def test_operation_unions_match_backend_contract_enums() -> None:
    """前端联合必须与 `muad_contracts.enums` 逐字对齐（未知值靠安全回退，不靠放宽类型）。"""
    service = _read(RUN_SERVICE)
    assert _frontend_union(service, "OperationStatus") == _backend_enum("OperationStatus")
    assert _frontend_union(service, "CompletionMode") == _backend_enum("CompletionMode")
    assert _frontend_union(service, "OperationErrorPhase") == _backend_enum("OperationErrorPhase")
    assert _frontend_union(service, "TaskStatus") == {
        "QUEUED",
        "RUNNING",
        "WAITING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    }


def test_unknown_task_status_is_not_shown_as_task_failure() -> None:
    """`task_status` 未知取值必须走安全回退：不得映射成任何已知状态（尤其「任务失败」）。"""
    options = _read(STATUS_OPTIONS)
    assert "taskStatusFallback" in options
    assert "run.operation.unknownTaskStatus" in options
    assert "run.operation.unknownStatus" in options
    table = _read(RUN_TABLE)
    assert "taskStatusFallback" in table
    for locale in LOCALES:
        payload = _locale(locale)
        fallback = payload["run.operation.unknownTaskStatus"]
        assert "失败" not in fallback, locale
        assert "{{status}}" in fallback, "回退文案必须带安全标识占位符"
        for status in ("QUEUED", "RUNNING", "WAITING", "COMPLETED", "FAILED", "CANCELLED"):
            assert payload[f"task.status.{status}"], (locale, status)


def test_run_operation_table_raises_intent_and_null_task_has_no_link() -> None:
    """表格只上抛点击意图：不导航、不裸 HTTP；`task_id` 为空时单元格与末列操作都不可点击。"""
    table = _read(RUN_TABLE)
    assert "onOpenTask" in table
    assert "useNavigate" not in table
    assert "navigate(" not in table
    assert "axios" not in table and "fetch(" not in table
    assert "record.task_id ? (" in table, "任务单元格必须先判 task_id"
    assert "record.task_id && props.onOpenTask ? (" in table, "末列操作必须先判 task_id 与回调"
    assert "run.operation.submissionPending" in table
    assert "run.operation.noTask" in table
    assert 'testId={`run-operation-task-${record.task_id}`}' in table
    assert 'testId={`run-operation-view-task-${record.task_id}`}' in table


def test_run_detail_hooks_invalidate_stale_responses() -> None:
    """关闭/切对象/卸载使过期响应失效；操作页签首次激活才取数、空页回落到合法页码。"""
    detail = _read(HOOK_DETAIL)
    assert "requestSeq" in detail
    assert "requestSeq.current += 1" in detail
    assert "current === requestSeq.current" in detail, "响应写回前必须核验代次"
    assert "loaded.runId === runId" in detail, "详情只在所属 run 匹配时可见，不闪旧对象"
    operations = _read(HOOK_OPERATIONS)
    assert "requestSeq" in operations
    assert "current !== requestSeq.current" in operations, "过期响应必须提前返回"
    assert "loadedPage.runId === runId" in operations, "清单只在所属 run 匹配时可见"
    assert "Math.ceil(value.total / targetSize)" in operations, "空页回退须计算最后合法页"
    assert "targetPage > lastPage" in operations
    assert "OPERATIONS_PAGE_SIZE_MAX" in operations
    sheet = _read(RUN_SHEET)
    assert "useRunDetail" in sheet and "useRunOperations" in sheet
    assert "getRun(" not in sheet and "listRunOperations(" not in sheet, "容器经 hooks 取数，不直调 service"


def test_related_detail_controller_keeps_single_related_layer() -> None:
    """RUN/TASK 判别联合互斥：同一时刻只挂一个关联面板，Run/Task 不递归堆叠。"""
    controller = _read(CONTROLLER)
    assert "{ kind: 'RUN'; runId: string }" in controller
    assert "{ kind: 'TASK'; taskId: string; sourceRunId: string }" in controller
    assert "<RunDetailSideSheet" in controller
    assert "<TaskDetailSideSheet" in controller
    assert controller.count("<RelatedDetailController") == 0, "控制器不得递归挂载自身"
    assert "useRelatedDetail" in controller
    audit = _read(AUDIT_DETAIL)
    page = _read(TASK_PAGE)
    for source in (audit, page):
        assert "<RelatedDetailController" in source
        assert "state={related.state}" in source
    # TaskPage 复用页面主 Task 面板：Run→Task 关闭关联层后设置主面板
    assert "related.close();" in page and "setDetailTaskId(taskId);" in page


def test_run_event_dynamic_keys_are_enumerated() -> None:
    timeline = _read(RUN_TIMELINE)
    event_types = _string_array(timeline, "RUN_EVENT_TYPES")
    assert REQUIRED_EVENT_TYPES.issubset(set(event_types)), REQUIRED_EVENT_TYPES - set(event_types)
    assert "run.event.unknown" in timeline
    for locale in LOCALES:
        payload = _locale(locale)
        assert payload["run.event.unknown"], locale
        for event_type in event_types:
            assert payload.get(f"run.event.{event_type}"), (locale, event_type)


def test_run_operation_dynamic_keys_are_enumerated() -> None:
    service = _read(RUN_SERVICE)
    options = _read(STATUS_OPTIONS)
    assert "run.operation.mode." in options
    assert "run.operation.errorPhase." in options
    for locale in LOCALES:
        payload = _locale(locale)
        for status in _frontend_union(service, "OperationStatus"):
            assert payload.get(f"run.operation.status.{status}"), (locale, status)
        for mode in _frontend_union(service, "CompletionMode"):
            assert payload.get(f"run.operation.mode.{mode}"), (locale, mode)
        for phase in _frontend_union(service, "OperationErrorPhase"):
            assert payload.get(f"run.operation.errorPhase.{phase}"), (locale, phase)
        for reason in _frontend_union(service, "WaitReason"):
            assert payload.get(f"run.detail.waitingReason.{reason}"), (locale, reason)


def test_run_module_components_do_not_use_naked_http() -> None:
    """HTTP 出口唯一：仅 `services/runs.ts` import 共享 api；组件/hooks 不得自建请求。"""
    for path in _module_sources():
        source = _read(path)
        assert "from 'axios'" not in source and "import axios" not in source, path
        assert not re.search(r"\bfetch\s*\(", source), path
        if path != RUN_SERVICE:
            assert "api.get(" not in source and "api.post(" not in source, path
    assert "from '../../../api/client'" in _read(RUN_SERVICE)


def test_run_module_times_use_datetime_text_and_translations_are_not_cached() -> None:
    for path in (RUN_SHEET, RUN_TABLE, RUN_TIMELINE):
        source = _read(path)
        assert "DateTimeText" in source, path
        assert "toLocaleString(" not in source and "toLocaleDateString(" not in source, path
    for path in _module_sources():
        source = _read(path)
        assert not CACHED_TRANSLATION.search(source), f"{path}: 译文不得缓存于模块态/state"
        assert "from '../../../i18n'" not in source, f"{path}: 不得直接消费 i18n 实例"
