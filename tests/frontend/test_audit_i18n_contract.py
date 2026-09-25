"""[B-209][RULE-i18n-001] 审计模块 i18n 词条覆盖与语言切换安全源码契约（设计 §3.3/§3.6）。

设计 §3.3 技术选型要求「所有文案 `t(key)`」，§3.6 三个视图状态（列表/详情/关联缺失）的文案全部
取自词条。仓库实际结构是**扁平 JSON + 点号键**（`apps/console-platform/frontend/src/locales`
下的 `zh-CN.json` 与 `en-US.json`，两侧共 682 条），**没有**按模块拆分的 TS 词条文件——任务书
Files 栏写的 `locales/zh-CN/audit-observability.ts` 与仓库现状不符，本契约以实际结构为准，不新造
一套结构出来。

`scripts/check_frontend_i18n.py` 只比对两份词条的键集，**看不到**动态键（模板串拼接）与「文案是否
真的经 catalog 码映射」；本文件补上这两处：

- 动态键变体必须齐备：`audit.auditType.${value}`、`audit.resourceType.${type}`、
  `audit.resultStatus.${code}`、`audit.detail.field.${field}`、`audit.export.error.${code}`。
  枚举口径取自**后端契约**（`audit_query_service.py` 的 `AUDIT_TYPES` / `RESULT_STATUSES`，Console
  列表/详情/导出共用同一校验口径）与模块自身的枚举数组，两侧必须一致——只写在词条里没人用、
  或代码里用了却没有词条，都判失败。
- 文案路径：错误码只经 `hooks/useAuditExport` 上抛（`errorCode`，hook 不含文案），组件用
  `EXPORT_ERROR_KEYS` 把 catalog 码映射成 i18n key，未登记的码落 `audit.export.errorFallback`；
  状态标签一律经 `StatusTag` 的 `options` + `audit.resultStatus.*` 词条，不裸渲染枚举值。

语言切换：公共 `LocaleSwitch` 调 `changeLocale` → `i18n.changeLanguage`，react-i18next 的
`useTranslation()` 默认订阅 `languageChanged` 并触发重渲染；因此本模块的组件要么自己调该 hook，
要么在一次渲染内由调用方注入 `t`（`AuditTable` 是 RemoteTable 入参工厂，不自己调 hook）。模块
作用域缓存译文、把译文放进 `useState`/`useMemo`/`useRef`、或直接 import i18n 实例取文案，都会
**绕过**切换（切了语言页面不变），本契约一并钉死。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "apps/console-platform/frontend/src"
MODULE = SRC / "modules/audit-observability"
LOCALES = SRC / "locales"
SHELL = SRC / "layout/AppLayout.tsx"
I18N = SRC / "i18n/index.ts"
APP = SRC / "App.tsx"
CATALOG = ROOT / "config/api-messages.yaml"
BE_QUERY = (
    ROOT
    / "apps/console-platform/backend/src/muad_console_platform/application/audit_query_service.py"
)

PAGE = MODULE / "pages/AuditPage.tsx"
FILTER_BAR = MODULE / "components/AuditFilterBar.tsx"
TABLE = MODULE / "components/AuditTable.tsx"
SHEET = MODULE / "components/AuditDetailSideSheet.tsx"
BUTTON = MODULE / "components/AuditExportButton.tsx"
HOOK_EXPORT = MODULE / "hooks/useAuditExport.ts"

# 设计 §3.3 的模块组件中，自己调 useTranslation() 的四处（AuditTable 是入参工厂，见下）
HOOK_COMPONENTS = (PAGE, FILTER_BAR, SHEET, BUTTON)

# 可见中文与全角字符：去掉注释后仍出现在字符串字面量里即视为硬编码文案
CJK = re.compile(r"[　-〿一-鿿！-～]")

# 静态键引用：t('audit.column.time')
T_KEY = re.compile(r"(?<![A-Za-z_])t\(\s*'([^']+)'")

# 在模块作用域或组件状态里缓存译文（语言切换后不再更新）
CACHED_TRANSLATION = re.compile(
    r"^const\s+\w+\s*(?::[^=]+)?=\s*t\("
    r"|use(?:State|Memo|Ref)(?:<[^>]*>)?\(\s*(?:\(\)\s*=>\s*)?t\(",
    re.MULTILINE,
)

# 直接消费 i18n 实例：不订阅 languageChanged，切换语言不会让本模块重渲染
I18N_INSTANCE = ("i18n.t(", "from '../../../i18n'", "from '../../i18n'")


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


def _module_sources() -> list[Path]:
    """模块内全部 `.ts/.tsx`：新增文件自动纳入扫描，不靠「已知的那几个文件」的硬编码清单。"""
    return sorted(path for path in MODULE.rglob("*") if path.suffix in {".ts", ".tsx"})


def _load_locale(name: str) -> dict[str, str]:
    payload = json.loads(_read(LOCALES / f"{name}.json"))
    assert all(isinstance(value, str) for value in payload.values()), (
        "词条须是扁平 JSON + 点号键（值为字符串），不得嵌套对象"
    )
    return payload


def _locales() -> tuple[dict[str, str], dict[str, str]]:
    return _load_locale("zh-CN"), _load_locale("en-US")


def _string_array(source: str, name: str) -> tuple[str, ...]:
    """取 `const NAME = ['A', 'B'] as const;` 的字面量枚举。"""
    match = re.search(rf"const\s+{name}\s*=\s*\[(.*?)\]\s*as const;", source, re.DOTALL)
    assert match, f"缺少枚举数组声明 {name}"
    return tuple(re.findall(r"'([^']+)'", match.group(1)))


def _object_block(source: str, name: str) -> str:
    """取 `const NAME... = {` 起至同层 `};` 的声明体。"""
    marker = f"const {name}"
    assert marker in source, f"缺少对象声明 {name}"
    body = source[source.index(marker) :]
    return body[: body.index("};")]


def _object_keys(block: str) -> tuple[str, ...]:
    return tuple(re.findall(r"^\s{2}(\w+)\s*:", block, re.MULTILINE))


def _object_string_values(block: str) -> tuple[str, ...]:
    return tuple(re.findall(r":\s*'([^']+)'", block))


def _type_fields(sheet: str) -> dict[str, tuple[str, ...]]:
    """四类审计的来源表字段映射（`audit.detail.field.${field}` 的枚举口径）。"""
    block = _object_block(sheet, "TYPE_FIELDS")
    return {
        name: tuple(re.findall(r"'([^']+)'", fields))
        for name, fields in re.findall(r"(\w+):\s*\[(.*?)\]", block, re.DOTALL)
    }


def _table_status_options(table: str) -> dict[str, str]:
    """表格 `StatusTag` 选项表：状态码 → 颜色。"""
    match = re.search(r"const statusOptions[^=]*=\s*\{(.*?)\n\s*\};", table, re.DOTALL)
    assert match, "缺少表格 StatusTag 选项表 statusOptions"
    return dict(re.findall(r"(\w+):\s*\{\s*color:\s*'(\w+)'", match.group(1)))


def _backend_frozenset(name: str) -> set[str]:
    """后端枚举口径（`audit_query_service.py`）——前端枚举必须与之一致。"""
    match = re.search(
        rf"^{name} = frozenset\(\{{(.*?)\}}\)", _read(BE_QUERY), re.MULTILINE | re.DOTALL
    )
    assert match, f"后端缺少 {name} 声明"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _enumerated_keys() -> set[str]:
    """模块内全部动态键的枚举变体（含导出兜底键）。"""
    bar = _read(FILTER_BAR)
    table = _read(TABLE)
    sheet = _read(SHEET)
    button = _read(BUTTON)
    fields = _type_fields(sheet)

    keys = {f"audit.auditType.{code}" for code in _string_array(bar, "AUDIT_TYPES")}
    keys |= {f"audit.auditType.{name}" for name in fields}
    keys |= {f"audit.resourceType.{code}" for code in _string_array(bar, "RESOURCE_TYPES")}
    keys |= {f"audit.resultStatus.{code}" for code in _string_array(bar, "RESULT_STATUSES")}
    keys |= {f"audit.resultStatus.{code}" for code in _object_keys(_object_block(sheet, "RESULT_COLORS"))}
    keys |= {f"audit.resultStatus.{code}" for code in _table_status_options(table)}
    for names in fields.values():
        keys |= {f"audit.detail.field.{field}" for field in names}
    keys |= {key for key in _static_keys(sheet) if key.startswith("audit.detail.field.")}
    keys |= set(_object_string_values(_object_block(button, "EXPORT_ERROR_KEYS")))
    keys |= {"audit.export.errorFallback"}
    return keys


def test_module_files_present() -> None:
    """模块源文件齐备（扫描面覆盖模块内全部 `.ts/.tsx`）。"""
    paths = _module_sources()
    for path in (PAGE, FILTER_BAR, TABLE, SHEET, BUTTON, HOOK_EXPORT):
        assert path in paths, f"缺少模块文件：{path}"
    assert len(paths) >= 10, f"模块文件数异常：{paths}"


def test_every_static_key_exists_in_both_locales() -> None:
    """[RULE-i18n-001] 模块内每一处 `t('key')` 的键在 zh-CN 与 en-US 双侧齐备。"""
    zh, en = _locales()
    referenced: set[str] = set()
    for path in _module_sources():
        referenced |= _static_keys(_read(path))
    # 非空过：五个文件各贡献至少一个静态键，解析失效时立刻暴露
    assert {
        "nav.audit",
        "audit.columns.time",
        "audit.filter.traceId",
        "audit.detail.tab.relations",
        "audit.export.action",
        "common.retry",
    } <= referenced, f"静态键解析疑似失效：{sorted(referenced)}"

    missing = {
        name: sorted(key for key in referenced if key not in payload)
        for name, payload in (("zh-CN", zh), ("en-US", en))
    }
    assert not any(missing.values()), f"词条缺失：{missing}"


def test_dynamic_key_variants_exist_in_both_locales() -> None:
    """[RULE-i18n-001] 动态键的枚举变体在双侧齐备（模板串拼接的键静态扫描看不见）。"""
    zh, en = _locales()
    variants = _enumerated_keys()
    # 非空过：五类动态键各留一个哨兵，解析失效时立刻暴露
    assert {
        "audit.auditType.MODEL",
        "audit.resourceType.PROJECT_PLATFORM",
        "audit.resultStatus.SUCCESS",
        "audit.detail.field.before",
        "audit.export.error.IDEMPOTENCY_MISMATCH",
        "audit.export.errorFallback",
    } <= variants, f"动态键枚举疑似失效：{sorted(variants)}"

    missing = {
        name: sorted(key for key in variants if key not in payload)
        for name, payload in (("zh-CN", zh), ("en-US", en))
    }
    assert not any(missing.values()), f"动态键变体缺失：{missing}"


def test_audit_key_sets_are_identical_and_all_referenced() -> None:
    """[RULE-i18n-001] 两侧 `audit.*` 键集一致（无缺键/无多余键），且每条都被模块引用。"""
    zh, en = _locales()
    zh_keys = {key for key in zh if key.startswith("audit.")}
    en_keys = {key for key in en if key.startswith("audit.")}
    assert zh_keys, "未解析到 audit.* 词条"
    assert zh_keys == en_keys, (
        f"键集不一致：仅 zh-CN={sorted(zh_keys - en_keys)}；仅 en-US={sorted(en_keys - zh_keys)}"
    )

    referenced = _enumerated_keys()
    for path in _module_sources():
        referenced |= _static_keys(_read(path))
    orphans = sorted(key for key in zh_keys if key not in referenced)
    assert not orphans, f"audit.* 词条无人引用（多余词条）：{orphans}"

    blank = sorted(key for key in zh_keys if not zh[key].strip() or not en[key].strip())
    assert not blank, f"空词条：{blank}"


def test_no_hardcoded_chinese_in_any_module_file() -> None:
    """[RULE-i18n-001] 模块全部源文件（含 service/types/hooks）不承载硬编码中文文案。"""
    offenders = {
        path.name: [text for text in _string_literals(_read(path)) if CJK.search(text)]
        for path in _module_sources()
    }
    assert not any(offenders.values()), f"出现硬编码中文文案：{offenders}"


def test_components_obtain_text_through_the_i18n_hook() -> None:
    """[RULE-i18n-001] 语言切换安全：组件经 hook 取文案，不把译文缓存起来。

    `AuditTable` 是 `RemoteTable` 入参工厂（设计 §3.3 CMP-02），由 `AuditPage` 在一次渲染内注入
    `t`；其余四处组件各自调 `useTranslation()`。切换语言时 react-i18next 触发重渲染、`t` 随之
    更新；模块作用域常量、`useState`/`useMemo`/`useRef` 里的译文与直接消费 i18n 实例都会绕过它。
    """
    for path in HOOK_COMPONENTS:
        assert "const { t } = useTranslation();" in _read(path), (
            f"{path.name} 未经 useTranslation() 取文案（切换语言不会重渲染）"
        )

    page = _read(PAGE)
    table = _read(TABLE)
    assert "t: TFunction" in table, "AuditTable 须以入参注入的方式取 t（不自己调 hook）"
    assert ",t}" in _compact(page), "页面须把本次渲染的 t 注入 buildAuditTableProps"

    for path in _module_sources():
        source = _read(path)
        cached = CACHED_TRANSLATION.findall(source)
        assert not cached, f"{path.name} 在模块作用域/组件状态里缓存译文：{cached}"
        for banned in I18N_INSTANCE:
            assert banned not in source, f"{path.name} 直接消费 i18n 实例，切换语言不重渲染：{banned}"


def test_language_switch_is_reachable_from_audit_pages() -> None:
    """[RULE-i18n-001] 审计路由落在挂载公共 `LocaleSwitch` 的壳层内 ⇒ 页面可切换语言。"""
    shell = _read(SHELL)
    app = _read(APP)
    assert "<LocaleSwitch />" in shell, "ConsoleShell 须挂载公共 LocaleSwitch"
    assert "i18n.changeLanguage(locale)" in _read(I18N), "切换须经 i18n 单例 changeLanguage"
    assert '<Route path="audits" element={<AuditPage />} />' in app, "审计路由须注册"
    assert app.index('path="audits"') > app.index("<AppLayout />"), "审计路由须落在壳层内"


def test_error_and_status_text_path_is_catalog_code_to_i18n_key() -> None:
    """[RULE-i18n-001] 错误/状态文案路径：catalog 码 → i18n key，不硬编码也不裸渲染枚举值。"""
    zh, en = _locales()
    button = _read(BUTTON)
    hook = _read(HOOK_EXPORT)
    table = _read(TABLE)
    sheet = _read(SHEET)

    block = _object_block(button, "EXPORT_ERROR_KEYS")
    mapping = dict(re.findall(r"(\w+):\s*'(audit\.export\.error\.[^']+)'", block))
    assert mapping, "缺少 catalog 码 → i18n key 映射表 EXPORT_ERROR_KEYS"

    catalog = yaml.safe_load(_read(CATALOG))["codes"]
    for code, key in sorted(mapping.items()):
        assert key == f"audit.export.error.{code}", f"词条键名须与 catalog 码同名：{code} → {key}"
        assert code in catalog, f"catalog 未登记错误码 {code}"
        for name, payload in (("zh-CN", zh), ("en-US", en)):
            assert key in payload, f"{name} 缺 {key}"
    assert "t(exportErrorKey(errorCode))" in button, "失败文案必须经码映射取值"
    for fallback in ("audit.export.errorFallback",):
        assert fallback in zh and fallback in en, f"缺兜底文案词条 {fallback}"

    assert "errorCode: string | null;" in hook, "错误码须原样上抛（hook 不承载文案）"
    assert not [text for text in _string_literals(hook) if CJK.search(text)], "hook 不得硬编码文案"
    assert "label:t('audit.resultStatus." in _compact(table), "表格状态标签须经 audit.resultStatus.* 词条"
    assert "t(`audit.resultStatus.${status}`)" in sheet, "详情状态标签须经 audit.resultStatus.* 词条"


def test_status_and_type_enumerations_match_backend_domain() -> None:
    """[B-209] 动态键的枚举口径取后端契约：词条与展示映射都要覆盖后端归一域。

    `audit_query_service.py` 的 `AUDIT_TYPES`/`RESULT_STATUSES` 是 Console 列表/详情/导出共用的
    参数校验口径，列表行可以带着其中任一值回来；前端缺词条或缺展示映射，`StatusTag` 就会原样
    渲染裸枚举码（英文界面里出现未本地化文案），筛选下拉也会少一个后端允许的取值。
    """
    backend_types = _backend_frozenset("AUDIT_TYPES")
    backend_statuses = _backend_frozenset("RESULT_STATUSES")
    bar = _read(FILTER_BAR)
    sheet = _read(SHEET)
    table = _read(TABLE)

    assert set(_string_array(bar, "AUDIT_TYPES")) == backend_types, "筛选栏审计类型枚举与后端不一致"
    assert set(_type_fields(sheet)) == backend_types, "详情来源字段映射未覆盖后端全部审计类型"
    assert set(_string_array(bar, "RESULT_STATUSES")) == backend_statuses, (
        "筛选栏执行结果枚举与后端不一致"
    )
    assert set(_object_keys(_object_block(sheet, "RESULT_COLORS"))) == backend_statuses, (
        "详情状态标签映射未覆盖后端全部执行结果"
    )
    assert set(_table_status_options(table)) == backend_statuses, (
        "表格状态标签映射未覆盖后端全部执行结果"
    )

    zh, en = _locales()
    for name, payload in (("zh-CN", zh), ("en-US", en)):
        keys = {f"audit.auditType.{code}" for code in backend_types}
        keys |= {f"audit.resultStatus.{code}" for code in backend_statuses}
        missing = sorted(key for key in keys if key not in payload)
        assert not missing, f"{name} 缺后端枚举对应的词条：{missing}"


def test_status_and_type_labels_are_localized_not_bare_codes() -> None:
    """[RULE-i18n-001] 状态/类型标签是本地化文案，不是裸枚举码，两侧也各不相同。

    术语类词条（`audit.resourceType.MCP`、`audit.columns.traceId` 等专有名词）两侧本就同名，
    不属本断言范围——只钉「必须翻译」的两组枚举。
    """
    zh, en = _locales()
    for prefix in ("audit.auditType.", "audit.resultStatus."):
        keys = sorted(key for key in zh if key.startswith(prefix))
        assert keys, f"缺少 {prefix} 词条"
        for key in keys:
            code = key[len(prefix) :]
            assert zh[key] != code and en[key] != code, f"{key} 是裸枚举码，未本地化"
            assert zh[key] != en[key], f"{key} 未本地化（两侧文案相同）"
