"""[S-07][E-07][RULE-ui-detail-001][B-207] 审计只读详情 SideSheet 与关联链接源码契约。

设计 §3.3 CMP-03、§3.4、§3.6。

复用公共 `DetailSideSheet`（标题/副标题居左、关闭 X 与其同行靠右由 Semi Header 承载）、公共
`EntityLink`/`ErrorState`/`DetailGrid`/`StatusTag`/`DateTimeText`/`EmptyState`。设计 §3.3 的
`DetailTabs` 由 `DetailSideSheet` 内建 `Tabs`（`Tabs.TabPane` children）承载——仓库不存在独立
的 DetailTabs 模块，冻结 verifier `test_detail_sidesheet_contract.py` 已断言该公共组件持有 Semi
Header/Tabs 与条件渲染的 actions，故本文件只补审计侧特有断言（不复述其检查项）。

只读：不向公共 SideSheet 传 `actions`、无编辑/提交入口（S-07「只读详情，无操作按钮」）。
详情状态机归 `hooks/useAuditDetail`（只经 TASK-010 的 service 层，组件不裸用 HTTP 客户端）；
文案只用 i18n key（RULE-i18n-001）。

[E-07] `related_missing` 为真时关联区渲染 `ErrorState` 且**不伪造**关联数据：该分支内不得出现
`EntityLink`、不得有 `??`/占位符回退；缺失判定必须取后端契约 `relatedMissing`（不得按 `related`
为空反推）。审计自身字段与来源字段照常展示（`renderBasicTab` 与关联区互不影响）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "apps/console-platform/frontend/src"
MODULE = SRC / "modules/audit-observability"
SHEET = MODULE / "components/AuditDetailSideSheet.tsx"
HOOK = MODULE / "hooks/useAuditDetail.ts"
PAGE = MODULE / "pages/AuditPage.tsx"
LOCALES = SRC / "locales"

# 可见中文与全角字符：去掉注释后仍出现在字符串字面量里即视为硬编码文案
CJK = re.compile(r"[　-〿一-鿿！-～]")

# 四类审计的来源表独有字段（后端 `_detail_extras` 的 camelCase 键，设计 §3.4）
TYPE_FIELDS = {
    "CONFIG": ("before", "after", "sourceIp"),
    "TOOL": ("argsPreview", "toolCallId", "toolKind", "preparedArgsHash", "errorCode"),
    "EGRESS": (
        "targetType",
        "adapterKey",
        "platformId",
        "method",
        "policyDecision",
        "statusCode",
        "errorCode",
    ),
    "MODEL": (
        "provider",
        "model",
        "attempt",
        "retryReason",
        "inputTokens",
        "outputTokens",
        "errorCode",
    ),
}

# 交互稿 §audit 详情「基本信息」的八个字段（复用列表列标题词条，不新增重复文案）
BASIC_FIELD_KEYS = (
    "audit.columns.time",
    "audit.columns.auditType",
    "audit.columns.actor",
    "audit.columns.agent",
    "audit.columns.target",
    "audit.columns.action",
    "audit.columns.result",
    "audit.columns.traceId",
)

# 详情自有词条：分区/页签/关联/审计自身扩展字段
DETAIL_KEYS = (
    "audit.detail.section.basic",
    "audit.detail.section.source",
    "audit.detail.tab.basic",
    "audit.detail.tab.relations",
    "audit.detail.field.auditId",
    "audit.detail.field.resourceType",
    "audit.detail.field.resourceId",
    "audit.detail.field.latencyMs",
    "audit.detail.related.run",
    "audit.detail.related.task",
    "audit.detail.relatedMissing",
    "audit.detail.noRelated",
)

# 后端 snake_case 只允许出现在 service 层
BACKEND_KEYS = (
    "audit_type",
    "resource_type",
    "resource_id",
    "actor_user_id",
    "result_status",
    "trace_id",
    "occurred_at",
    "latency_ms",
    "related_missing",
    "args_preview",
    "policy_decision",
)

BANNED_HTTP = ("api/client", "axios", "fetch(")

# 只读详情不得出现的交互入口（S-07）：公共 Header 的关闭 X 是唯一控件
BANNED_ACTIONS = (
    "actions=",
    "Button",
    "FormModal",
    "ConfirmAction",
    "Popconfirm",
    "IconPlus",
    "onEdit",
    "onDelete",
    "onSubmit",
    "createExport",
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


def _branch(source: str, marker: str) -> str:
    """取 `marker` 起至同级缩进 `  }` 的分支体（压缩空白，便于「分支内不得出现 X」断言）。"""
    assert marker in source, f"缺少分支：{marker}"
    body = source[source.index(marker) + len(marker) :]
    end = body.find("\n  }")
    assert end != -1, f"{marker} 分支未闭合"
    return _compact(body[:end])


def _function(source: str, name: str) -> str:
    """取 `function <name>(...)` 的实现（到下一个顶层 export 声明为止）。"""
    marker = f"function {name}("
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


def _locale(name: str) -> dict[str, str]:
    return json.loads((LOCALES / f"{name}.json").read_text(encoding="utf-8"))


def test_module_files_exist() -> None:
    """TASK-013 的两个交付文件齐备（详情组件与详情状态机）。"""
    _read(SHEET)
    _read(HOOK)


def test_sidesheet_reuses_shared_detail_components() -> None:
    """[RULE-ui-detail-001] 复用公共 `DetailSideSheet`（Header/Tabs 归其承载），不自造壳层。"""
    sheet = _read(SHEET)
    assert "components/common/DetailSideSheet" in sheet, "详情必须复用公共 DetailSideSheet"
    assert "<DetailSideSheet" in sheet
    compact = _compact(sheet)
    assert "title={" in compact and "subtitle={" in compact, "标题/副标题须交给公共 SideSheet 居左渲染"
    assert "onCancel={props.onClose}" in compact, "关闭 X 由公共 SideSheet 的 Header 行承载"
    assert "activeTab={" in compact and "onTabChange={setActiveTab}" in compact

    # Tabs 层：设计 §3.3 的 `DetailTabs` 由公共 DetailSideSheet 内建 Tabs 的 children 承载
    assert "<Tabs.TabPane" in sheet
    for item_key in ("basic", "relations"):
        assert f'itemKey="{item_key}"' in sheet, f"缺少详情页签 {item_key}"

    # 不得自行 import Semi SideSheet / 自带 Header 或关闭按钮（冻结 verifier 断言其归公共组件）
    assert re.search(r"import\s*\{[^}]*\bSideSheet\b", sheet) is None, "不得直接使用 Semi SideSheet"
    for banned in ("IconClose", "semi-sidesheet-header", "headerStyle", "footer="):
        assert banned not in sheet, f"详情壳层归公共 DetailSideSheet，不得出现 {banned}"


def test_sidesheet_renders_all_four_audit_type_sections() -> None:
    """[B-207] 四类审计的来源字段均按 `detail.extras` 渲染，标签一律 i18n key。"""
    sheet = _read(SHEET)
    compact = _compact(sheet)

    assert "TYPE_FIELDS[detail.auditType]" in compact, "来源字段须按 auditType 取映射"
    assert "detail.extras[field]" in compact, "来源字段必须取自详情出参 extras，不得猜表"
    assert "t(`audit.detail.field.${field}`)" in sheet, "来源字段标签须为 i18n key"
    assert "JSON.stringify(value,null,2)" in compact, "对象型字段（before/after/argsPreview）须 JSON 化"

    for audit_type, fields in TYPE_FIELDS.items():
        assert f"{audit_type}:[" in compact, f"类型映射缺少 {audit_type}"
        for field in fields:
            assert f"'{field}'" in compact, f"{audit_type} 缺少字段 {field}"

    # 审计自身字段（交互稿八列）与既有公共组件复用
    for key in BASIC_FIELD_KEYS:
        assert f"t('{key}')" in sheet, f"基础信息缺少字段文案 {key}"
    for component in ("DetailGrid", "StatusTag", "DateTimeText"):
        assert component in sheet, f"详情须复用公共 {component}"
    assert "t(`audit.auditType.${detail.auditType}`)" in sheet, "审计类型文案须走 i18n 枚举"

    # 自身字段与来源字段都在「基础信息」页签内渲染（不受关联可读性影响）
    assert "renderBasicTab(detail,loading,t)" in compact, "基础信息页签须渲染自身字段与来源字段"


def test_related_missing_renders_error_state_without_fabrication() -> None:
    """[E-07] 关联不可读：关联区渲染 `ErrorState`，不落链接、不编造关联 id。"""
    sheet = _read(SHEET)
    branch = _branch(sheet, "if (detail.relatedMissing) {")
    assert "ErrorState" in branch, "关联不可读须渲染 ErrorState"
    assert "EntityLink" not in branch, "不可读时不得渲染任何关联链接"
    assert "??" not in branch and "'-'" not in branch, "不可读时不得用占位符/回退伪装关联数据"
    assert "DetailGrid" not in branch and "renderBasicTab" not in branch, "关联缺失不得影响审计自身字段"
    assert "Object.keys(" not in _compact(sheet), "缺失判定须取后端 relatedMissing，不得按 related 为空反推"


def test_relation_links_use_entity_link_and_are_navigable() -> None:
    """[S-07] 关联链接用公共 `EntityLink`，点击落到承载 Run/Task 记录的任务列表并携带 id。"""
    sheet = _read(SHEET)
    compact = _compact(sheet)
    assert "components/common/EntityLink" in sheet
    assert sheet.count("<EntityLink") == 2, "Run/Task 各一条关联链接"
    assert '"audit-related-run"' in sheet and '"audit-related-task"' in sheet, "关联链接须有稳定 testId"
    assert "detail.related.runId" in compact and "detail.related.taskId" in compact, (
        "关联 id 必须取自详情出参 related"
    )
    assert "useNavigate" in compact, "关联链接须可跳转（路由归容器）"
    assert "/tasks?${relation}Id=${id}" in compact, "关联须落到承载 Run/Task 记录的任务列表并携带 id"
    assert "EmptyState" in sheet, "无关联记录时须用公共 EmptyState 提示"


def test_hook_owns_detail_state_with_race_guard() -> None:
    """[B-207] `useAuditDetail` 持有详情状态：race guard、关闭即失效在途响应、失败只置 failed。"""
    hook = _read(HOOK)
    state = _block(hook, "export interface AuditDetailState {")
    for member in ("detail:AuditDetail|null;", "loading:boolean;", "failed:boolean;", "reload():void;"):
        assert member in state, f"AuditDetailState 缺少 {member}"

    body = _compact(_function(hook, "useAuditDetail"))
    assert "constcurrent=++requestSeq.current;" in body, "须以 requestSeq 标记本次请求"
    assert "if(current!==requestSeq.current){return;}" in body, "乱序响应须在应用前丢弃"
    assert body.count("if(current===requestSeq.current){") == 2, "失败/收尾分支不得被过期响应改写"
    assert "requestSeq.current+=1;" in body, "关闭/切换选择须让在途响应失效（不得回写已关闭详情）"
    assert "setFailed(true)" in _catch_block(hook), "失败只置 failed（不伪造详情数据）"
    assert "voidreload();" in body, "选择变更即取详情"
    assert "return{detail,loading,failed,reload};" in body, "须暴露重试出口"


def test_hook_and_component_reach_backend_only_through_service() -> None:
    """[RULE-front-001] 取数只经 TASK-010 的 service：组件经 hook，hook 经 service，无裸请求。"""
    hook = _read(HOOK)
    assert "from '../services/auditService'" in hook
    assert "getAudit(auditType,auditId)" in _compact(hook)
    for banned in BANNED_HTTP:
        assert banned not in hook, f"不得出现裸请求：{banned}"
    for backend_key in BACKEND_KEYS:
        assert backend_key not in hook, f"hook 不得出现后端 snake_case 字段 {backend_key}"

    sheet = _read(SHEET)
    assert "from '../hooks/useAuditDetail'" in sheet, "组件须经详情状态机取数"
    for banned in BANNED_HTTP + ("auditService",):
        assert banned not in sheet, f"组件不得直接取数：{banned}"
    for backend_key in BACKEND_KEYS:
        assert backend_key not in sheet, f"组件不得出现后端 snake_case 字段 {backend_key}"


def test_sidesheet_is_read_only() -> None:
    """[S-07] 只读：不传 `actions`、无编辑/提交/导出入口，唯一控件是公共 Header 的关闭 X。"""
    sheet = _read(SHEET)
    code = _strip_comments(sheet)
    for banned in BANNED_ACTIONS:
        assert banned not in code, f"只读详情不得出现操作入口：{banned}"
    assert "onCancel={props.onClose}" in _compact(code), "关闭出口须交给公共 SideSheet"


def test_page_wires_detail_selection_into_sidesheet() -> None:
    """设计 §3.5：页面的 `{auditType, auditId}` 选择态驱动详情 SideSheet，关闭即清空。"""
    page = _read(PAGE)
    compact = _compact(page)
    assert "from '../components/AuditDetailSideSheet'" in page
    assert "<AuditDetailSideSheet" in page
    assert "auditType={detail.auditType}" in compact and "auditId={detail.auditId}" in compact, (
        "详情须同时携带 auditType 与 auditId（4 张来源表 UUID 不互通）"
    )
    assert "onClose={handleCloseDetail}" in compact
    assert "setDetail(null)" in compact, "关闭须清空选择态"
    assert "onOpenDetail" in page, "列表入口仍须经 onOpenDetail 落选择态"


def test_no_hardcoded_chinese_in_new_files() -> None:
    """[RULE-i18n-001] 新增详情/状态机不承载硬编码文案：字符串字面量不得含中文/全角字符。"""
    for path in (SHEET, HOOK):
        offenders = [text for text in _string_literals(_read(path)) if CJK.search(text)]
        assert not offenders, f"{path.name} 出现硬编码中文文案：{offenders}"


def test_i18n_keys_translated_in_both_locales() -> None:
    """[RULE-i18n-001] 详情静态词条 + 四类来源字段词条在 zh-CN/en-US 均齐备。"""
    sheet = _read(SHEET)
    keys = set(re.findall(r"(?<![A-Za-z_])t\(\s*'([^']+)'", sheet))
    keys |= {
        f"audit.detail.field.{field}" for fields in TYPE_FIELDS.values() for field in fields
    }
    keys |= set(DETAIL_KEYS)
    assert keys, "未解析到任何 i18n key"

    for locale in ("zh-CN", "en-US"):
        payload = _locale(locale)
        missing = sorted(key for key in keys if not payload.get(key))
        assert not missing, f"{locale} 缺少词条：{missing}"
