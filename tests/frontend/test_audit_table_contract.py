"""[B-206][S-06] 审计表格与列表数据 hook 源码契约（设计 §3.3 CMP-02、§3.5、§3.6；docs/15 §2 字段词典）。

列集合与 docs/15「运行审计」段的 UI 名称/字段口径一致（审计类型/操作目标/操作用户/Agent/动作/
执行结果/Trace ID/时间），执行结果复用 `StatusTag`、时间复用 `DateTimeText`，主展示字段提供打开
详情的 `EntityLink` seam（TASK-013 消费）；列表状态机归 `useAuditList`，取数只经 TASK-010 的
service 层，`requestSeq` 丢弃乱序响应、失败只置 `failed` 并可重试；分页状态由服务端响应驱动
（`total` 为服务端总数），footer 联动由 `AuditTable` 供给公共 `RemoteTable`。

仓库级冻结 verifier `test_ui_style_contract.py::test_module_list_pages_use_remote_table` 要求每个
`modules/**/*Page.tsx` 保留公共 `RemoteTable`，故 `AuditTable` 以「RemoteTable 入参工厂」形态供给
列/行渲染/空错槽位/分页，页面渲染 `RemoteTable` 并原样展开其产物（不整表替换、不重复套壳）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "apps/console-platform/frontend/src"
MODULE = SRC / "modules/audit-observability"
PAGE = MODULE / "pages/AuditPage.tsx"
TABLE = MODULE / "components/AuditTable.tsx"
HOOK = MODULE / "hooks/useAuditList.ts"
REMOTE_TABLE = SRC / "components/common/RemoteTable.tsx"

# 可见中文与全角字符：去掉注释后仍出现在字符串字面量里即视为硬编码文案
CJK = re.compile(r"[　-〿一-鿿！-～]")

# docs/15 §2「运行审计」段：UI 字段名 → 列使用的 camelCase 字段（顺序即交互稿列序，设计 §3.7）
COLUMN_FIELDS = (
    ("audit.columns.time", "时间", ("occurredAt",)),
    ("audit.columns.auditType", "审计类型", ("auditType",)),
    ("audit.columns.actor", "操作用户", ("actorName", "actorUserId")),
    ("audit.columns.agent", "Agent", ("agentName", "agentId")),
    ("audit.columns.target", "操作目标", ("resourceId",)),
    ("audit.columns.action", "动作", ("action",)),
    ("audit.columns.result", "执行结果", ("resultStatus",)),
    ("audit.columns.traceId", "Trace ID", ("traceId",)),
)

# 执行结果标签语义：`SUCCESS` 成功 / `FAILED` 失败；未知领域错误码由 StatusTag 原样降级为灰标
RESULT_STATUS_COLORS = (("SUCCESS", "green"), ("FAILED", "red"))

# 后端 snake_case 只允许出现在 service 层
BACKEND_KEYS = (
    "audit_type",
    "resource_type",
    "resource_id",
    "actor_user_id",
    "result_status",
    "trace_id",
    "occurred_at",
    "page_size",
)

BANNED_HTTP = ("api/client", "axios", "fetch(")

COLUMNS_FN = "function buildAuditColumns("
PROPS_FN = "export function buildAuditTableProps("


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _block(source: str, header: str) -> str:
    """取 `header` 声明起至下一个顶格 `}` 的声明体（压缩空白，便于字段断言）。"""
    assert header in source, f"缺少声明：{header}"
    body = source[source.index(header) + len(header) :]
    end = body.find("\n}")
    assert end != -1, f"{header} 声明未闭合"
    return _compact(body[:end])


def _object(source: str, header: str) -> str:
    """取 `header` 起至同级 `  };` 的对象字面量（压缩空白，便于入参断言）。"""
    assert header in source, f"缺少声明：{header}"
    body = source[source.index(header) + len(header) :]
    end = body.find("\n  };")
    assert end != -1, f"{header} 对象未闭合"
    return _compact(body[:end])


def _columns_source(source: str) -> str:
    """取列定义区：`buildAuditColumns` 起至入参组装 `buildAuditTableProps`（列须独立于组装）。"""
    assert COLUMNS_FN in source and PROPS_FN in source, "缺少列定义/入参组装函数"
    start = source.index(COLUMNS_FN)
    end = source.index(PROPS_FN)
    assert start < end, "列定义须独立成函数并先于入参组装"
    return _compact(source[start:end])


def _function(source: str, name: str) -> str:
    """取 `export function <name>(...)` 的实现（到下一个顶层 export 声明为止）。"""
    marker = f"export function {name}("
    assert marker in source, f"缺少函数：{name}"
    body = source[source.index(marker) :]
    following = body.find("\nexport ", 1)
    return body if following == -1 else body[:following]


def _catch_block(source: str) -> str:
    """取查询失败分支（`} catch {` 至 `} finally {`）的实现。"""
    marker = "} catch {"
    assert marker in source, "缺少查询失败分支"
    body = source[source.index(marker) + len(marker) :]
    end = body.find("} finally {")
    return _compact(body[:end] if end != -1 else body)


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def _string_literals(source: str) -> list[str]:
    groups = re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"|`([^`\n]*)`", _strip_comments(source))
    return [text for group in groups for text in group if text]


def test_module_files_exist() -> None:
    """TASK-012 的两个交付文件齐备（表格与状态机）。"""
    _read(TABLE)
    _read(HOOK)


def test_columns_match_docs15_field_names() -> None:
    """[S-06] 列集合与 docs/15 口径一致，列序与交互稿一致（设计 §3.7）。"""
    columns = _columns_source(_read(TABLE))
    assert columns, "列定义须独立成函数，便于口径断言"

    for key, ui_name, fields in COLUMN_FIELDS:
        assert f"t('{key}')" in columns, f"缺少「{ui_name}」列标题 {key}"
        for field in fields:
            assert f"dataIndex:'{field}'" in columns or f"record.{field}" in columns, (
                f"「{ui_name}」列未使用 docs/15 字段 {field}"
            )

    order = [columns.index(f"t('{key}')") for key, _, _ in COLUMN_FIELDS]
    assert order == sorted(order), "列顺序须与交互稿一致（设计 §3.7）"
    assert columns.count("dataIndex:") == len(COLUMN_FIELDS), "列集合不得多出未登记字段"


def test_columns_reuse_status_and_datetime_components() -> None:
    """[RULE-time-001][S-06] 执行结果复用 `StatusTag`、时间复用 `DateTimeText`。"""
    table = _read(TABLE)
    assert "components/common/DateTimeText" in table
    assert "components/common/StatusTag" in table and "StatusTagOption" in table
    assert "<DateTimeText value={value} />" in table
    assert "<StatusTag status={value} options={statusOptions} />" in table
    assert "DATE_TIME_FORMAT" not in table, "时间格式统一由 DateTimeText 承载"

    compact = _compact(table)
    for status, color in RESULT_STATUS_COLORS:
        assert f"{status}:{{color:'{color}',label:t('audit.resultStatus.{status}')}}" in compact, (
            f"执行结果 {status} 须映射到 {color} 标签 + i18n 文案"
        )
    assert "fallback" not in table, "未知领域错误码由 StatusTag 原样降级，不伪造文案"


def test_audit_table_feeds_remote_table_and_page_keeps_remote_table() -> None:
    """[RULE-ui-001] AuditTable 供给 RemoteTable 入参；页面保留公共 RemoteTable（冻结 verifier）。"""
    table = _read(TABLE)
    assert "components/common/RemoteTable" in table and "RemoteTableProps" in table, (
        "AuditTable 须以 RemoteTable 的入参契约供给列表"
    )
    props = _object(table, "return {")
    for member in (
        "rowKey:",
        "columns:",
        "dataSource:options.items",
        "loading:options.loading",
        "empty:",
    ):
        assert member in props, f"AuditTable 未向 RemoteTable 供给 {member}"

    page = _read(PAGE)
    assert "components/common/RemoteTable" in page, "页面必须继续经公共 RemoteTable 渲染列表"
    assert "<RemoteTable" in page and "buildAuditTableProps" in page, (
        "页面须渲染 RemoteTable 并原样展开 AuditTable 供给的入参"
    )
    remote_table = _read(REMOTE_TABLE)
    assert "PaginationFooter" in remote_table, "右下分页统一由 RemoteTable 内建"


def test_empty_and_error_slots_come_from_audit_table() -> None:
    """[E-06] 空态提供「清筛选」、失败态提供重试，槽位由 AuditTable 供给（设计 §3.6）。"""
    table = _read(TABLE)
    assert "components/common/EmptyState" in table and "components/common/ErrorState" in table
    assert "options.failed?" in _compact(table), "失败态须按 failed 呈现 ErrorState"
    assert "<ErrorState onRetry={options.onRetry} />" in table
    assert "t('common.empty')" in table and "t('common.emptyHint')" in table
    assert "t('common.reset')" in table and "options.onReset" in _compact(table), (
        "空态须提供「清筛选」出口"
    )


def test_primary_display_field_has_detail_open_seam() -> None:
    """[RULE-ui-001] 主展示字段（docs/15「操作目标」）提供打开详情的 seam，供 TASK-013 消费。"""
    table = _read(TABLE)
    options = _block(table, "export interface AuditTableOptions {")
    assert "onOpenDetail(item:AuditListItem):void;" in options, "详情入口须作为显式入参 seam 导出"
    assert "items:AuditListItem[];" in options

    assert "components/common/EntityLink" in table
    compact = _compact(table)
    # 主展示字段与 Trace ID 均经 EntityLink 打开详情（设计 §3.3.1）
    assert compact.count("onClick={()=>onOpenDetail(record)}") == 2
    assert "columns:buildAuditColumns(t,options.onOpenDetail)" in _object(table, "return {"), (
        "详情 seam 须由入参原样注入列定义"
    )
    assert "dataIndex:'resourceId'" in compact, "主展示字段须为「操作目标」列"
    assert "audit-link-" in table and "audit-trace-" in table, "两处入口须有稳定的 testId"


def test_hook_owns_list_state_and_guards_out_of_order_responses() -> None:
    """[S-06] `useAuditList` 持有列表状态：race guard、失败只置 failed、暴露 retry。"""
    hook = _read(HOOK)
    state = _block(hook, "export interface AuditListState {")
    for member in (
        "items:AuditListItem[];",
        "page:number;",
        "pageSize:number;",
        "total:number;",
        "loading:boolean;",
        "failed:boolean;",
        "reload():void;",
    ):
        assert member in state, f"AuditListState 缺少 {member}"

    body = _compact(_function(hook, "useAuditList"))
    assert "constcurrent=++requestSeq.current;" in body, "须以 requestSeq 标记本次请求"
    assert "if(current!==requestSeq.current){return;}" in body, "乱序响应须在应用前丢弃"
    assert body.count("if(current===requestSeq.current){") == 2, (
        "失败/收尾分支不得被过期响应改写"
    )
    assert "setFailed(true)" in _catch_block(hook), "失败只置 failed（保留筛选与已加载数据）"
    assert "voidreload();" in body, "首挂载与 query 变更即重取"
    assert "return{items,page,pageSize,total,loading,failed,reload};" in body, "须暴露 retry 出口"


def test_hook_reaches_backend_only_through_service_layer() -> None:
    """[RULE-front-001] 取数只经 TASK-010 的 service；表格与状态机都不裸用 HTTP 客户端。"""
    hook = _read(HOOK)
    assert "from '../services/auditService'" in hook
    assert "listAudits(query)" in _compact(hook)

    for source in (hook, _read(TABLE)):
        for banned in BANNED_HTTP:
            assert banned not in source, f"不得出现裸请求：{banned}"
        for backend_key in BACKEND_KEYS:
            assert backend_key not in source, f"不得出现后端 snake_case 字段 {backend_key}"


def test_pagination_state_is_server_driven() -> None:
    """[S-06] 分页状态由服务端响应驱动：page/pageSize/total 取自响应，footer 与 total 联动。"""
    hook = _read(HOOK)
    assert re.search(r"setPage\(\s*result\.page\s*\)", hook), "页码须回落到服务端响应"
    assert re.search(r"setPageSize\(\s*result\.pageSize\s*\)", hook), "页大小须回落到服务端响应"
    assert re.search(r"setTotal\(\s*result\.total\s*\)", hook), "total 须来自服务端响应"
    assert "items.length" not in hook and "Math.ceil" not in hook, "不得用本地数组推算总数/页数"

    props = _object(_read(TABLE), "return {")
    for binding in ("page:options.page", "pageSize:options.pageSize", "total:options.total"):
        assert binding in props, f"RemoteTable 分页入参缺少 {binding}"
    assert "onPageChange:" in props and "onPageSizeChange:" in props
    assert "setQuery" not in _read(TABLE), "翻页只上抛，不改写页面筛选状态"


def test_page_consumes_hook_and_table_state() -> None:
    """设计 §3.5：列表数据 `useAuditList` → `AuditTable`；页面只编排筛选/分页/详情状态。"""
    page = _read(PAGE)
    assert "from '../hooks/useAuditList'" in page
    table_props = _object(page, "const tableProps: AuditTableProps = {")
    for member in ("items,", "loading,", "page,", "pageSize,", "total,"):
        assert member in table_props, f"tableProps 缺少 {member}"
    assert "onOpenDetail:handleOpenDetail" in table_props

    compact = _compact(page)
    assert "AuditTableProps{" in compact, "设计 §3.4 的表格入参契约仍由页面导出"
    assert "setQuery((prev)=>({...prev,page,pageSize}))" in compact, "翻页须回到页面筛选状态"


def test_no_hardcoded_chinese_in_new_files() -> None:
    """[RULE-i18n-001] 新增表格/状态机不承载硬编码文案：字符串字面量不得含中文/全角字符。"""
    for path in (TABLE, HOOK):
        offenders = [text for text in _string_literals(_read(path)) if CJK.search(text)]
        assert not offenders, f"{path.name} 出现硬编码中文文案：{offenders}"
