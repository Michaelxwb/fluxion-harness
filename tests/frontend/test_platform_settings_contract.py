"""[契约机检] Console 系统设置页（TASK-010 / B-06、B-08）。

覆盖三类**可静态判定**的事实：

1. **HTTP 只经服务层**（RULE-front-001）：`modules/settings/` 下只有 `services/settingsApi.ts` 允许
   import 共享 `api/client`；组件、hook、页面不得出现 axios/fetch 或直接 import 客户端。
2. **字段控件由 API 元数据驱动**（RISK-FE-01）：CMP-05 的取值/范围/枚举/默认值只来自 `meta`，
   组件内不得写死默认值或范围字面量——前端不得成为第二套默认源。
3. **词条齐备**（RULE-i18n-001 / B-06）：直接 import 后端 `platform_settings_catalog` 遍历每个
   `label_key`/`unit_key`，断言 zh-CN 与 en-US 两侧都有词条；`applies_to` 五个生效方式标签齐全。

真实边界：**真实源码树 + 真实词条文件 + 真实后端 catalog 模块**（无服务）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from muad_console_platform.application.platform_settings_catalog import (
    build_groups,
    readonly_notes,
)
from muad_contracts.platform_settings import PlatformSettings

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend/src"
MODULE = FRONTEND / "modules/settings"
APPLIES_TO_SOURCE = MODULE / "appliesTo.ts"

#: `applies_to` 五个取值（后端 catalog 与前端映射共用同一枚举）。
APPLIES_TO_VALUES = ["new_run", "new_task", "next_operation", "restart_required", "code"]


def _locales() -> dict[str, dict[str, object]]:
    return {
        locale: json.loads((FRONTEND / f"locales/{locale}.json").read_text(encoding="utf-8"))
        for locale in ("zh-CN", "en-US")
    }


def _module_files() -> list[Path]:
    return sorted(MODULE.rglob("*.ts")) + sorted(MODULE.rglob("*.tsx"))


def test_settings_http_only_through_services_layer() -> None:
    """组件/hook/页面不得裸用 axios/fetch，也不得自建 api 客户端；只有 service 层 import 共享实例。"""
    service = MODULE / "services/settingsApi.ts"
    sources = _module_files()
    assert service in sources, "缺少 service 层"
    assert sources, "settings 模块源码为空：提取规则可能已与目录结构脱节"

    for path in sources:
        text = path.read_text(encoding="utf-8")
        if path == service:
            assert "from '../../../api/client'" in text, "service 必须 import 共享 api 客户端"
            continue
        assert "from 'axios'" not in text and 'from "axios"' not in text, f"{path} 裸用 axios"
        assert not re.search(r"\bfetch\s*\(", text), f"{path} 裸用 fetch"
        assert "api/client" not in text, f"{path} 只能在 service 层 import 共享 api 客户端"


def test_settings_field_control_reads_backend_metadata() -> None:
    """CMP-05 的控件形态与范围/枚举/默认值只来自 `meta`，不写死字面量。"""
    row = (MODULE / "components/SettingsFieldRow.tsx").read_text(encoding="utf-8")
    for token in (
        "meta.labelKey",
        "meta.min",
        "meta.max",
        "meta.enum",
        "meta.defaultValue",
        "meta.appliesTo",
    ):
        assert token in row, f"字段行未按元数据渲染，缺少 {token}"
    assert "controlKind(meta)" in row, "控件形态必须由元数据的 type/enum 推导"
    # 禁止在控件上写死默认值/范围字面量（例如 min={2} / max={1}）
    assert not re.search(r"\bmin=\{\s*\d", row), "不得写死范围下界字面量"
    assert not re.search(r"\bmax=\{\s*\d", row), "不得写死范围上界字面量"


def test_settings_page_does_not_hardcode_field_or_group_lists() -> None:
    """分组与字段必须由 API 返回的元数据渲染，页面不得内联字段清单。

    单面板布局（左导航 + 右面板）下，页面不再 `state.groups.map` 平铺分组：导航的 `groups`
    与面板的 `activeGroup` 都直接来自 `state.groups`，这里断言这条数据来源，防止页面自持
    一份分组清单。
    """
    page = (MODULE / "pages/SettingsPage.tsx").read_text(encoding="utf-8")
    assert "groups={state.groups}" in page, "分组导航必须由元数据渲染"
    assert "state.groups.find" in page, "当前面板分组必须由元数据派生"
    assert "SettingsGroupPanel" in page
    # 页面不得出现具体字段路径字面量（如 'snip.max_groups'），否则即第二套字段清单。
    assert "snip." not in page, "页面不得内联字段路径"


def test_settings_page_has_four_ui_states() -> None:
    """loading / empty / error / success 四态齐（§3.6）。"""
    page = (MODULE / "pages/SettingsPage.tsx").read_text(encoding="utf-8")
    assert "state.loading" in page and "settings-loading" in page, "缺少 loading 态"
    assert "state.failed" in page and "ErrorState" in page, "缺少 error 态"
    assert "state.revision === 0" in page and "settings.empty" in page, "缺少 revision=0 的 empty 态"
    assert "groups={state.groups}" in page, "缺少 success 态分组渲染"
    # 错误态必须在分组渲染之前 return：失败时不渲染任何值。
    failed_index = page.index("if (state.failed)")
    assert failed_index < page.index("groups={state.groups}"), "错误态必须先于分组渲染返回（不渲染任何值）"


def test_save_uses_revision_and_maps_field_errors() -> None:
    """保存带 `revision` 乐观并发；冲突显式提示；校验错误映射到字段（E-11/E-12）。"""
    hook = (MODULE / "hooks/usePlatformSettings.ts").read_text(encoding="utf-8")
    assert "revision: snapshot.revision" in hook, "保存必须携带当前 revision"
    assert "VERSION_CONFLICT" in hook and "setConflict(true)" in hook, "冲突必须显式置位"
    assert "fieldErrorsFrom" in hook, "校验失败必须映射到字段"
    assert "buildDocument" in hook, "保存必须提交整份分组文档"


def test_applies_to_labels_cover_all_five_values() -> None:
    """B-06：五个生效方式标签齐全，且 zh-CN/en-US 两侧都有词条。"""
    source = APPLIES_TO_SOURCE.read_text(encoding="utf-8")
    locales = _locales()
    for value in APPLIES_TO_VALUES:
        assert f"'{value}'" in source, f"生效方式映射缺少取值 {value}"
        key = f"settings.appliesTo.{value}"
        for locale, payload in locales.items():
            assert payload.get(key), f"{locale} 缺少生效方式词条 {key}"


def test_backend_catalog_label_keys_are_translated_in_both_locales() -> None:
    """遍历后端 catalog 的每个 label_key / unit_key，断言两侧词条齐备（最省事又最硬的覆盖）。"""
    groups = build_groups(PlatformSettings(), overrides={})
    assert groups, "catalog 未返回分组：导入或构造规则可能已与后端脱节"
    required: set[str] = set()
    for group in groups:
        required.add(group["label_key"])
        assert group["fields"], f"分组 {group['key']} 无字段"
        for field in group["fields"]:
            required.add(field["label_key"])
            if field.get("unit_key"):
                required.add(field["unit_key"])
    for note in readonly_notes():
        required.add(note["label_key"])
        required.add(note["note_key"])

    locales = _locales()
    for locale, payload in locales.items():
        missing = sorted(key for key in required if not payload.get(key))
        assert missing == [], f"{locale} 缺少后端 label_key 词条: {missing}"


def test_backend_applies_to_values_are_known_to_frontend() -> None:
    """后端 catalog 用到的 `applies_to` 取值必须都在前端映射枚举内（新增取值须先补映射与词条）。"""
    groups = build_groups(PlatformSettings(), overrides={})
    used = {group["applies_to"] for group in groups}
    source = APPLIES_TO_SOURCE.read_text(encoding="utf-8")
    for value in sorted(used):
        assert f"'{value}'" in source, f"前端映射缺少后端正在使用的生效方式 {value}"


def test_settings_route_is_role_guarded_and_menu_has_entry() -> None:
    """B-08：菜单十一项含系统设置（adminOnly），路由受 RequireRole role=\"ADMIN\" 守卫。"""
    menu = (FRONTEND / "config/menu.ts").read_text(encoding="utf-8")
    keys = re.findall(r"key:\s*'([^']+)'", menu)
    assert len(keys) == 11 and keys[-1] == "nav.settings", "菜单必须是十一项且末项为系统设置"
    assert re.search(
        r"\{\s*path:\s*'/settings',\s*key:\s*'nav.settings',\s*adminOnly:\s*true\s*\}", menu
    )

    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    assert re.search(
        r'path="settings"[\s\S]{0,200}?<RequireRole role="ADMIN">[\s\S]{0,120}?<SettingsPage',
        app,
    ), "/settings 路由必须由 RequireRole role=\"ADMIN\" 守卫"
