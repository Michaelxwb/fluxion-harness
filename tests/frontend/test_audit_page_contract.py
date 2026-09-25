"""[RULE-ui-001][E-06][B-205] 审计列表页容器、路由/菜单与筛选栏源码契约（设计 §3.2/§3.3/§3.3.1/§3.6）。

左主操作（导出）/右上搜索筛选/右下分页；筛选条件映射为 `AuditListQuery`（page 重置为 1）；
查询失败保留筛选并呈现 `ErrorState`；文案只用 i18n key；API 只经 TASK-010 的 service 层。

ConsoleShell 在 01-platform-foundation 落地的真实组件名是 `layout/AppLayout.tsx`（设计 §3.2 的
「ConsoleShell」为文档名），因此壳层断言落在路由层，页面自身不重复套壳。

TASK-012 把列表数据状态机归 `hooks/useAuditList.ts`、列表体（列/行渲染/空错槽位/分页联动）归
`components/AuditTable.tsx`（以 RemoteTable 入参工厂形态供给，页面保留 `RemoteTable` 渲染）；
涉及这两处的断言改落在其归属文件，E-06 与「无裸请求」语义不变。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "apps/console-platform/frontend/src"
MODULE = SRC / "modules/audit-observability"
PAGE = MODULE / "pages/AuditPage.tsx"
FILTER_BAR = MODULE / "components/AuditFilterBar.tsx"
TABLE = MODULE / "components/AuditTable.tsx"
HOOK = MODULE / "hooks/useAuditList.ts"
APP = SRC / "App.tsx"
MENU = SRC / "config/menu.ts"
SHELL = SRC / "layout/AppLayout.tsx"
REMOTE_TABLE = SRC / "components/common/RemoteTable.tsx"
LOCALES = SRC / "locales"

# 可见中文与全角字符：去掉注释后仍出现在字符串字面量里即视为硬编码文案
CJK = re.compile(r"[　-〿一-鿿！-～]")

# 八个筛选控件 → `AuditListQuery` 字段（「时间」一个控件映射 startTime/endTime）
FILTER_FIELDS = (
    ("time", ("startTime", "endTime")),
    ("auditType", ("auditType",)),
    ("actorUserId", ("actorUserId",)),
    ("agent", ("resourceType",)),
    ("resourceId", ("resourceId",)),
    ("action", ("action",)),
    ("resultStatus", ("resultStatus",)),
    ("traceId", ("traceId",)),
)

# 后端 snake_case 只允许出现在 service 层
BACKEND_KEYS = (
    "audit_type",
    "resource_type",
    "resource_id",
    "actor_user_id",
    "result_status",
    "trace_id",
    "start_time",
    "end_time",
    "page_size",
)

ENUM_KEYS = (
    "audit.auditType.CONFIG",
    "audit.auditType.TOOL",
    "audit.auditType.EGRESS",
    "audit.auditType.MODEL",
    "audit.resultStatus.SUCCESS",
    "audit.resultStatus.FAILED",
    "audit.resourceType.AGENT",
    "audit.resourceType.SKILL",
    "audit.resourceType.MCP",
    "audit.resourceType.MODEL",
    "audit.resourceType.PROJECT_PLATFORM",
    "audit.resourceType.USER",
    "audit.resourceType.GRANT",
)


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


def _locale(name: str) -> dict[str, str]:
    return json.loads((LOCALES / f"{name}.json").read_text(encoding="utf-8"))


def test_module_files_exist() -> None:
    """页面容器与筛选栏齐备。"""
    _read(PAGE)
    _read(FILTER_BAR)


def test_route_registered_under_console_shell() -> None:
    """[RULE-ui-001] `/audits` 路由替换占位页，并挂载在 ConsoleShell（AppLayout）内。"""
    app = _read(APP)
    assert "import { AuditPage } from './modules/audit-observability/pages/AuditPage';" in app
    assert '<Route path="audits" element={<AuditPage />} />' in app
    assert 'element={<PlaceholderPage titleKey="nav.audit" />}' not in app, "占位页须被替换"
    assert '<AppLayout />' in app and "<RequireAuth>" in app
    assert app.index('path="audits"') > app.index("<AppLayout />"), "审计路由必须落在 ConsoleShell 布局内"


def test_menu_entry_registered() -> None:
    """固定 10 项菜单中的 `/audits` 项与导航图标齐备。"""
    assert "{ path: '/audits', key: 'nav.audit' }" in _read(MENU)
    shell = _read(SHELL)
    assert re.search(r"'/audits':\s*<Icon\w+", shell), "ConsoleShell 导航缺少 /audits 菜单项图标"
    for locale in ("zh-CN", "en-US"):
        assert _locale(locale).get("nav.audit"), f"{locale} 缺少 nav.audit"


def test_page_composes_shell_toolbar_and_pagination() -> None:
    """[RULE-ui-001] 页面骨架：ModuleToolbar（左上操作/右上筛选）+ 列表（右下分页由 RemoteTable 内建）。

    列表体的列/行渲染/空错槽位/分页入参由 TASK-012 的 `AuditTable` 供给（页面保留 `RemoteTable`
    渲染与筛选/分页/详情编排），故这些断言落在供给方文件。
    """
    page = _read(PAGE)
    for component in (
        "PageSection",
        "ModuleToolbar",
        "AuditFilterBar",
        "RemoteTable",
    ):
        assert component in page, f"列表页缺少公共组件 {component}"
    assert "actions={" in page, "缺少工具栏左侧主操作位"
    assert "search={<AuditFilterBar" in _compact(page), "筛选栏必须落在工具栏右侧搜索位"
    assert "AUDIT_PAGE_SIZE_DEFAULT" in page, "初始 pageSize 须取 service 层默认值"
    assert "AppLayout" not in page, "壳层由路由承载，页面不得重复套壳"
    assert "onOpenDetail" in page, "主展示字段须可点开详情（seam 由页面导出后交给 AuditTable）"
    assert "buildAuditTableProps" in page, "列表入参须由 AuditTable 供给后原样展开给 RemoteTable"
    assert "PaginationFooter" in _read(REMOTE_TABLE), "右下分页统一由 RemoteTable 内建 PaginationFooter"

    table = _read(TABLE)
    for piece in ("EmptyState", "ErrorState", "EntityLink", "onPageSizeChange"):
        assert piece in table, f"AuditTable 须为 RemoteTable 供给 {piece}"


def test_page_exports_props_contracts_for_followup_tasks() -> None:
    """设计 §3.4：表格/详情组件的 props 契约由容器导出，供 TASK-012/013 满足。"""
    source = _read(PAGE)
    table = _block(source, "export interface AuditTableProps {")
    for member in (
        "items:AuditListItem[];",
        "loading:boolean;",
        "page:number;",
        "pageSize:number;",
        "total:number;",
        "onPageChange(page:number,pageSize:number):void;",
        "onOpenDetail(item:AuditListItem):void;",
    ):
        assert member in table, f"AuditTableProps 缺少 {member}"

    detail = _block(source, "export interface AuditDetailSideSheetProps {")
    for member in (
        "visible:boolean;",
        "auditType:AuditListItem['auditType'];",
        "auditId:string|null;",
        "onClose():void;",
    ):
        assert member in detail, f"AuditDetailSideSheetProps 缺少 {member}"
    assert "setDetail(" in _compact(source), "页面须持有详情选择态（设计 §3.5）"


def test_left_main_action_slot_is_reserved_for_export() -> None:
    """[RULE-ui-001] 左主操作位留给 TASK-014 的导出按钮，本页不实现导出。"""
    page = _read(PAGE)
    assert "TASK-014" in page, "左主操作位须显式标注导出按钮归属 TASK-014"
    assert "createExport" not in page, "导出提交归 TASK-014，本页只预留主操作位"
    assert "audits/exports" not in page


def test_filter_bar_declares_eight_filters_mapped_to_query() -> None:
    """[RULE-ui-001] 筛选栏声明八个筛选控件，并映射到 `AuditListQuery`（camelCase）。"""
    source = _read(FILTER_BAR)
    props = _block(source, "export interface AuditFilterBarProps {")
    for member in (
        "value:AuditListQuery;",
        "onChange(patch:Partial<AuditListQuery>):void;",
        "onSearch():void;",
        "onReset():void;",
        "onRefresh():void;",
    ):
        assert member in props, f"AuditFilterBarProps 缺少 {member}"

    for field, keys in FILTER_FIELDS:
        assert f'data-testid="audit-filter-{field}"' in source, f"缺少筛选控件 {field}"
        for key in keys:
            assert f"{key}:" in _compact(source), f"筛选控件 {field} 未映射到 {key}"

    for backend_key in BACKEND_KEYS:
        assert backend_key not in source, f"筛选栏不得出现后端 snake_case 字段 {backend_key}"
    assert "auditService" not in source, "筛选栏只上抛筛选条件，不直接调 service"


def test_filter_change_resets_page_to_one() -> None:
    """设计 §3.4：任一筛选变更都把 page 重置为 1（单一出口保证不留例外）。"""
    source = _read(FILTER_BAR)
    assert "onChange({...patch,page:1})" in _compact(source), "筛选变更必须重置 page=1"
    assert source.count("emit({") >= len(FILTER_FIELDS), "八个筛选控件都须经统一 emit 出口上抛"
    assert source.count("props.onChange(") == 1, "只允许一个筛选上抛出口"
    assert "...patch" in _compact(_read(PAGE)), "页面须合并筛选补丁而非整体替换"


def test_reset_clears_filters_and_refresh_requeries() -> None:
    """设计 §3.3.1：重置清空筛选、刷新按当前页重查。"""
    bar = _read(FILTER_BAR)
    assert "props.onReset()" in bar and "props.onRefresh()" in bar
    assert "common.reset" in bar and "common.refresh" in bar

    page = _compact(_read(PAGE))
    assert "constDEFAULT_QUERY:AuditListQuery={page:1,pageSize:AUDIT_PAGE_SIZE_DEFAULT};" in page
    assert "setQuery(DEFAULT_QUERY)" in page, "重置须回到不含任何筛选项的初始查询"
    assert "onRefresh={handleRefresh}" in page and "onReset={handleReset}" in page
    assert "handleRefresh" in page and "voidreload()" in page, "刷新须重取当前页"


def test_error_path_renders_error_state_and_keeps_filters() -> None:
    """[E-06] 查询失败：`ErrorState` + 重试，且筛选条件与已加载数据保留（不空白页）。

    TASK-012 后取数状态机归 `hooks/useAuditList`（失败只置 `failed`），`ErrorState` 槽位由
    `AuditTable` 供给；页面仍持有筛选栏、清筛选与重试出口。断言语义与原版一致，仅改归属文件。
    """
    hook = _read(HOOK)
    catch = _catch_block(hook)
    assert "setFailed(true)" in catch
    assert "setQuery" not in catch, "查询失败必须保留筛选条件（不得重置 query）"
    assert "setItems" not in catch and "setTotal" not in catch, "查询失败不得清空已加载数据"
    assert "listAudits(query)" in _compact(hook), "失败分支须来自 service 取数"

    table = _read(TABLE)
    assert "options.failed?" in _compact(table), "错误态须在列表区按 failed 呈现"
    assert "ErrorState" in table and "onRetry={options.onRetry}" in table
    assert "EmptyState" in table and "options.onReset" in _compact(table), "空态须提供「清筛选」出口"

    page = _read(PAGE)
    assert "AuditFilterBar" in page, "错误态下工具栏与筛选栏仍须渲染"
    assert "onRetry: handleRefresh" in page and "onReset={handleReset}" in page, (
        "重试/清筛选出口由页面供给"
    )


def test_page_calls_service_layer_not_api_client() -> None:
    """[RULE-front-001] 列表取数经 TASK-010 的 service（TASK-012 后由 `useAuditList` 收口）；
    组件不裸用 api client/axios/fetch。"""
    hook = _read(HOOK)
    assert "from '../services/auditService'" in hook
    assert "listAudits(" in hook

    page = _read(PAGE)
    assert "useAuditList" in page, "页面经列表状态机取数，不直接调 service"
    for source in (page, hook, _read(FILTER_BAR), _read(TABLE)):
        assert "api/client" not in source
        assert "axios" not in source
        assert "fetch(" not in source


def test_no_hardcoded_chinese_in_module_files() -> None:
    """[RULE-i18n-001] 新增页面/筛选栏不承载硬编码文案：字符串字面量不得含中文/全角字符。"""
    for path in (PAGE, FILTER_BAR):
        offenders = [text for text in _string_literals(_read(path)) if CJK.search(text)]
        assert not offenders, f"{path.name} 出现硬编码中文文案：{offenders}"


def test_i18n_keys_translated_in_both_locales() -> None:
    """[RULE-i18n-001] 页面/筛选栏/表格引用的静态 key 与枚举文案在 zh-CN/en-US 均齐备。"""
    sources = _read(PAGE) + _read(FILTER_BAR) + _read(TABLE)
    keys = set(re.findall(r"(?<![A-Za-z_])t\(\s*'([^']+)'", sources))
    assert keys, "未解析到任何 i18n key"
    for locale in ("zh-CN", "en-US"):
        payload = _locale(locale)
        missing = sorted(key for key in keys if not payload.get(key))
        assert not missing, f"{locale} 缺少词条：{missing}"
        missing_enum = [key for key in ENUM_KEYS if not payload.get(key)]
        assert not missing_enum, f"{locale} 缺少枚举文案：{missing_enum}"
