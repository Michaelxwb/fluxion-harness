"""[B-214] 审计前端接线收口契约（设计 §3.3 CMP-04、§3.6 UI 状态）。

TASK-019 收口时登记了三处前端口径缺口，本契约把它们钉成源码边界（B-214 的唯一归属）：

- **「Agent」筛选**：设计 §3.3 的「Agent」指审计行的 `agent_id`/`agent_name`（运行类记录有、config 类
  为空），不是资源类型。TASK-021 已给 API-01/API-05 加上 `agent_id` 参数，筛选项必须端到端接它
  （`types.AuditListQuery.agentId` → `toListParams` 的 `agent_id` → 导出请求体的 `agent_id`），
  不再拿 `resource_type` 冒充；`resource_type` 仍是独立的「资源类型」筛选项。
- **`resource_type` 值域开放**：后端投影列的实际取值由配置侧写入器（`AUDIT_*` 常量与内联字面量，
  含小写形态）与运行侧投影字面量 + egress `target_type` 共同决定。筛选项须覆盖这些取值，详情
  「资源类型」行须按取值取词条、未登记取值原样展示（不空白、不编造文案）。
- **刷新失败非破坏性**：首载失败（无行可保留）由列表整页 `ErrorState` 承载；刷新失败（已有行）须保留
  已加载行并就地给 `Banner` 提示 + 显式重试，与导出失败同一形态。

值域派生不写死：配置侧从 `muad_console_platform/application` 的写入器取（`AUDIT_*` 常量 + 内联
`resource_type="..."`，后者覆盖 `platform_service`/`credential_service` 的小写取值），运行侧从投影 SQL
的两处字面量与 egress 写入器的 `EGRESS_TARGET_TYPE` 取；两侧解析失效都会立刻断言失败。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "apps/console-platform/frontend/src"
MODULE = SRC / "modules/audit-observability"
LOCALES = SRC / "locales"

TYPES = MODULE / "types.ts"
SERVICE = MODULE / "services/auditService.ts"
FILTER_BAR = MODULE / "components/AuditFilterBar.tsx"
SHEET = MODULE / "components/AuditDetailSideSheet.tsx"
HOOK = MODULE / "hooks/useAuditList.ts"
PAGE = MODULE / "pages/AuditPage.tsx"

# 变更面（含 service/types/hook）：硬编码文案与词条覆盖都按这一组扫描
CHANGED_FILES = (TYPES, SERVICE, FILTER_BAR, SHEET, HOOK, PAGE)

CONSOLE_APP = ROOT / "apps/console-platform/backend/src/muad_console_platform"
REPOSITORY = CONSOLE_APP / "infrastructure/repositories/audit_query_repository.py"
EGRESS_WRITER = (
    ROOT / "apps/agent-runtime/src/muad_agent_runtime/application/mcp_runtime_adapter.py"
)

# 设计文档 v1.5 登记的三个历史写法：后端写入器从未产出（实际为小写 `project_platform`、
# `PLATFORM_USER`、`AGENT_ACCESS_GRANT`），但既有契约与词条已把它们计入模块登记域。
LEGACY_ALIASES = frozenset({"PROJECT_PLATFORM", "USER", "GRANT"})

# 运行侧投影字面量：TOOL/MODEL 两分支的 `resource_type` 是投影里的字面量，不是来源表列
RUNTIME_LITERALS = ("TOOL", "MODEL")

# 可见中文与全角字符：去掉注释后仍出现在字符串字面量里即视为硬编码文案
CJK = re.compile(r"[　-〿一-鿿！-～]")

# 静态键引用：t('audit.filter.agent')
T_KEY = re.compile(r"(?<![A-Za-z_])t\(\s*'([^']+)'")

# 资源类型取值来源：模块常量与内联字面量（`AUDIT_TYPES` 是 frozenset，不会被常量式匹配）
AUDIT_CONST = re.compile(r"^AUDIT_[A-Z_]*\s*=\s*\"([A-Za-z_]+)\"", re.MULTILINE)
INLINE_RESOURCE_TYPE = re.compile(r"resource_type\s*=\s*\"([A-Za-z_]+)\"")
EGRESS_TARGET_CONST = re.compile(r"^EGRESS_TARGET_TYPE\s*=\s*\"([A-Za-z_]+)\"", re.MULTILINE)


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def _string_literals(source: str) -> list[str]:
    groups = re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"|`([^`\n]*)`", _strip_comments(source))
    return [text for group in groups for text in group if text]


def _static_keys(source: str) -> set[str]:
    """静态 `t('key')` 引用（去注释后抽取）。"""
    return set(T_KEY.findall(_strip_comments(source)))


def _block(source: str, header: str) -> str:
    """取 `header` 声明起至下一个顶格 `}` 的声明体（压缩空白，便于成员断言）。"""
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


def _window(source: str, start: str, end: str) -> str:
    """取 `start` 起至其后第一个 `end` 的窗口（压缩空白），用于单个筛选控件的归属断言。"""
    assert start in source, f"缺少锚点：{start}"
    begin = source.index(start)
    finish = source.index(end, begin + len(start))
    return _compact(source[begin:finish])


def _string_array(source: str, name: str) -> tuple[str, ...]:
    """取 `const NAME = ['A', 'B'] as const;` 的字面量枚举。"""
    match = re.search(rf"const\s+{name}\s*=\s*\[(.*?)\]\s*as const;", source, re.DOTALL)
    assert match, f"缺少枚举数组声明 {name}"
    return tuple(re.findall(r"'([^']+)'", match.group(1)))


def _locale(name: str) -> dict[str, str]:
    payload = json.loads(_read(LOCALES / f"{name}.json"))
    assert all(isinstance(value, str) for value in payload.values()), (
        "词条须是扁平 JSON + 点号键（值为字符串）"
    )
    return payload


def _config_resource_types() -> set[str]:
    """配置侧资源类型：Console 各 AppService 写入 `config_audit_log.resource_type` 的实际取值。

    两种写法都覆盖：模块常量（`AUDIT_RESOURCE_TYPE = "AGENT"`，`skill_service` 的
    `_record_audit(...)` 也以位置实参传常量）与内联字面量（`resource_type="project_platform"`，
    覆盖小写形态）。凡 `AUDIT_*` 字符串常量都按资源类型计。
    """
    values: set[str] = set()
    for path in sorted((CONSOLE_APP / "application").glob("*.py")):
        source = _read(path)
        values |= set(AUDIT_CONST.findall(source))
        values |= set(INLINE_RESOURCE_TYPE.findall(source))
    assert values, "配置侧资源类型解析疑似失效"
    return values


def _runtime_resource_types() -> set[str]:
    """运行侧资源类型：投影 SQL 的字面量 + egress 写入器落库的 `target_type`。

    TOOL/MODEL 两分支的 `resource_type` 是投影 SQL 里的字面量；EGRESS 分支取
    `egress_audit.target_type`，其唯一写入点声明了 `EGRESS_TARGET_TYPE`。
    """
    sql = _read(REPOSITORY)
    literals = {value for value in RUNTIME_LITERALS if f"'{value}'," in sql}
    assert literals == set(RUNTIME_LITERALS), "投影 SQL 的运行侧资源类型字面量解析疑似失效"
    egress = set(EGRESS_TARGET_CONST.findall(_read(EGRESS_WRITER)))
    assert egress, "egress 写入器缺少 target_type 常量"
    return literals | egress


def _actual_resource_types() -> set[str]:
    """后端投影列 `resource_type` 的实际取值域（配置侧 ∪ 运行侧）。"""
    return _config_resource_types() | _runtime_resource_types()


def test_b214_agent_filter_is_wired_to_agent_id_end_to_end() -> None:
    """[B-214] 「Agent」筛选端到端接 `agentId`：types → 列表查询参数 → 导出请求体。"""
    query = _block(_read(TYPES), "export interface AuditListQuery {")
    assert "agentId?:string;" in query, "AuditListQuery 须有 agentId 筛选字段"

    service = _read(SERVICE)
    list_params = _block(service, "function toListParams(")
    assert "agent_id:query.agentId," in list_params, "列表查询须把 agentId 映射为后端 agent_id"
    assert "resource_type:query.resourceType," in list_params, (
        "resourceType 仍是独立筛选项，不得被 Agent 顶替"
    )
    export_body = _block(service, "function toExportBody(")
    assert "agent_id:filters.agentId," in export_body, "导出请求体须带上 agentId（后端 DTO 已支持）"

    bar = _read(FILTER_BAR)
    agent_control = _window(bar, 'data-testid="audit-filter-agent"', "/>")
    assert "value={value.agentId??''}" in agent_control, "Agent 筛选控件须绑定 agentId"
    assert "emit({agentId:text||undefined})" in agent_control, "Agent 筛选须上抛 agentId"
    assert "resourceType" not in agent_control, "Agent 筛选不得再冒充 resourceType"

    resource_control = _window(bar, 'data-testid="audit-filter-resourceType"', "/>")
    assert "value={value.resourceType??undefined}" in resource_control, (
        "资源类型筛选控件须绑定 resourceType"
    )
    assert "emit({resourceType:(rawasstring)??undefined})" in resource_control, (
        "资源类型筛选须上抛 resourceType"
    )


def test_b214_resource_type_options_cover_backend_domain() -> None:
    """[B-214] 筛选项覆盖后端投影列的实际取值域，且不引入值域外的取值。"""
    options = set(_string_array(_read(FILTER_BAR), "RESOURCE_TYPES"))
    assert options, "缺少资源类型枚举 RESOURCE_TYPES"

    actual = _actual_resource_types()
    assert len(actual) >= 15, f"值域派生疑似失效（过窄）：{sorted(actual)}"
    missing = sorted(actual - options)
    assert not missing, f"筛选项未覆盖后端实际取值：{missing}"

    extra = sorted(options - actual - LEGACY_ALIASES)
    assert not extra, f"筛选项出现值域外的取值（词条无处可依）：{extra}"

    assert "optionList={RESOURCE_TYPES.map(" in _compact(_read(FILTER_BAR)), (
        "筛选下拉取值须取 RESOURCE_TYPES"
    )


def test_b214_detail_resource_type_row_is_localized_with_raw_fallback() -> None:
    """[B-214] 详情「资源类型」行：登记取值走词条，未登记取值原样展示（不空白、不编造）。"""
    sheet = _read(SHEET)
    label = _block(sheet, "function resourceTypeLabel(")
    assert "RESOURCE_TYPE_VALUES.has(value)" in label, "须按登记域判定是否有词条"
    assert "t(`audit.resourceType.${value}`)" in label, "登记取值须走 audit.resourceType.* 词条"
    assert "text(value)" in label, "未登记取值须原样展示（兜底，不留空白）"
    assert "RESOURCE_TYPES" in sheet, "详情行与筛选栏共用同一资源类型登记域"

    compact = _compact(sheet)
    assert "resourceTypeLabel(t,detail.resourceType)" in compact, (
        "详情「资源类型」行须经 resourceTypeLabel 取值"
    )
    assert "text(detail.resourceType)" not in compact, "不得裸渲染 resourceType（未知值会留空）"


def test_b214_refresh_failure_keeps_rows_with_notice_but_first_load_uses_error_state() -> None:
    """[B-214] 首载失败 → 整页 `ErrorState`；刷新失败 → 保留已加载行 + 就地提示 + 重试。"""
    hook = _read(HOOK)
    catch = _catch_block(hook)
    assert "setFailed(true)" in catch, "失败只置 failed"
    assert "setItems" not in catch and "setTotal" not in catch, "失败不得清空已加载行"
    assert "listAudits(query)" in _compact(hook), "失败分支来自 service 取数"

    page = _compact(_read(PAGE))
    assert "refreshFailed" in page and "firstLoadFailed" in page, "须显式区分两种失败"
    assert "constrefreshFailed=failed&&items.length>0;" in page, (
        "刷新失败 = 取数失败且已有行可保留"
    )
    assert "constfirstLoadFailed=failed&&!refreshFailed;" in page, "首载失败 = 取数失败且无行"
    assert "failed:firstLoadFailed," in page, "表格整页 ErrorState 只由首载失败驱动"
    assert "Banner" in page, "刷新失败须用非破坏性内联提示（与导出失败同形态）"
    assert "refreshFailed?<AuditRefreshNotice" in page, "提示须按刷新失败态渲染"
    assert 'data-testid="audit-refresh-error"' in page, "提示须有稳定 testId"
    assert 'data-testid="audit-refresh-retry"' in page, "提示须有显式重试入口"
    assert "onClick={props.onRetry}" in _compact(_read(PAGE)), "重试入口须复用刷新出口"


def test_b214_new_i18n_keys_exist_in_both_locales() -> None:
    """[RULE-i18n-001] 变更面引用的词条在中英文两侧齐备，且资源类型值域词条全量登记。"""
    zh = _locale("zh-CN")
    en = _locale("en-US")
    assert set(zh) == set(en), "zh-CN/en-US 词条集合必须一致"

    referenced: set[str] = set()
    for path in CHANGED_FILES:
        referenced |= _static_keys(_read(path))
    for key in (
        "audit.filter.agent",
        "audit.filter.resourceType",
        "audit.list.refreshFailed",
        "common.retry",
    ):
        assert key in referenced, f"变更面应引用词条 {key}"

    domain = set(_string_array(_read(FILTER_BAR), "RESOURCE_TYPES"))
    referenced |= {f"audit.resourceType.{value}" for value in domain}

    missing = {
        name: sorted(key for key in referenced if not payload.get(key))
        for name, payload in (("zh-CN", zh), ("en-US", en))
    }
    assert not any(missing.values()), f"词条缺失：{missing}"


def test_b214_changed_files_carry_no_hardcoded_chinese() -> None:
    """[RULE-i18n-001] 变更面（service/types/hook 在内）不承载硬编码文案。"""
    offenders = {
        path.name: [text for text in _string_literals(_read(path)) if CJK.search(text)]
        for path in CHANGED_FILES
    }
    assert not any(offenders.values()), f"出现硬编码中文文案：{offenders}"
