from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend"
SRC = FRONTEND / "src"

HEX_PATTERN = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_theme_entrypoint_and_tokens() -> None:
    main = _read(SRC / "main.tsx")
    assert "AppProviders" in main
    assert "'./styles/app.css'" in main
    assert "'@douyinfe/semi-ui/dist/css/semi.min.css'" in main

    providers = _read(SRC / "components/common/AppProviders.tsx")
    assert "ConfigProvider" in providers
    assert "locale/source/zh_CN" in providers
    assert "locale/source/en_US" in providers
    assert "useTranslation()" in providers, "Semi locale 必须随界面语言变化重新渲染"
    assert "semiLocaleFor(" in providers, "缺少按语言选择 Semi locale 的映射"
    assert re.search(r"startsWith\('en'\)", providers), "必须按 en* 语言选择英文 locale"
    assert "SEMI_LOCALES['en-US']" in providers and "SEMI_LOCALES['zh-CN']" in providers
    assert re.search(r"locale=\{(semiLocale|semiLocaleFor\()", providers), "Semi locale 必须按语言动态选择"
    assert not re.search(r"locale=\{(zhCN|zh_CN)\}", providers), "Semi locale 不能写死单一语言"

    index = _read(FRONTEND / "index.html")
    assert 'theme-mode="dark"' in index, "默认暗色主题"

    css = _read(SRC / "styles/app.css")
    assert "--semi-color-primary:" in css
    assert "--app-radius:" in css
    assert "body[theme-mode='dark']" in css

    theme = _read(SRC / "theme.ts")
    assert "'dark'" in theme and "theme-mode" in theme


def test_pages_use_console_page_shell_and_no_raw_colors() -> None:
    page_files = sorted((SRC / "pages").glob("*.tsx")) + sorted((SRC / "modules").rglob("*.tsx"))
    assert page_files
    offenders = [
        f"{path.relative_to(ROOT)}: raw hex color"
        for path in page_files
        if HEX_PATTERN.search(_read(path))
    ]
    assert offenders == [], "颜色必须走 styles/app.css 的 token: " + ", ".join(offenders)

    for name in (
        "pages/PlaceholderPage.tsx",
        "modules/agent-management/AgentPage.tsx",
        "modules/user-identity/UserPage.tsx",
    ):
        source = _read(SRC / name)
        assert "PageHeader" in source and "PageSection" in source, f"{name} 必须使用 ConsolePage 骨架"
        assert "PageCard" not in source

    remote_table = _read(SRC / "components/common/RemoteTable.tsx")
    assert "PaginationFooter" in remote_table, "列表分页统一走 PaginationFooter"

    console_page = _read(SRC / "components/common/ConsolePage.tsx")
    assert "export function PageHeader" in console_page
    assert "export function PageSection" in console_page


def test_branding_has_no_legacy_name() -> None:
    for locale in ("zh-CN", "en-US"):
        payload = json.loads(_read(SRC / f"locales/{locale}.json"))
        assert payload["app.title"].startswith("Fluxion")
        assert payload["app.brand"] == "fluxion"
    index = _read(FRONTEND / "index.html")
    assert "Fluxion Console" in index
    assert "MSS" not in index


def test_semi_components_are_the_only_ui_library() -> None:
    package = json.loads(_read(FRONTEND / "package.json"))
    dependencies = package["dependencies"]
    assert "@douyinfe/semi-ui" in dependencies
    banned = ("antd", "@mui/material", "element-plus", "@chakra-ui/react")
    assert not [name for name in banned if name in dependencies]


# 非列表的模块页（仪表盘等）：规则文案只约束**列表页**（「左上操作 + 右上搜索筛选 + 列表 +
# 右下分页」），仪表盘没有工具栏/表格/分页，故不受 RemoteTable 约束。例外必须**显式声明**，
# 且下方会反查它确实不含任何列表构件——不能靠"不写 RemoteTable"混过去。
NON_LIST_MODULE_PAGES = (
    "apps/console-platform/frontend/src/modules/overview-dashboard/pages/OverviewPage.tsx",
    "apps/console-platform/frontend/src/modules/settings/pages/SettingsPage.tsx",
)


def test_module_list_pages_use_remote_table() -> None:
    list_pages = sorted((SRC / "modules").rglob("*Page.tsx"))
    assert list_pages, "至少应存在一个模块列表页"
    exceptions = set(NON_LIST_MODULE_PAGES)
    offenders = [
        str(path.relative_to(ROOT))
        for path in list_pages
        if str(path.relative_to(ROOT)) not in exceptions and "RemoteTable" not in _read(path)
    ]
    assert offenders == [], "模块列表页必须使用 RemoteTable（内建 PaginationFooter）: " + ", ".join(offenders)

    for relative in sorted(exceptions):
        body = _read(ROOT / relative)
        assert "ModuleToolbar" not in body, f"{relative} 声明为非列表页，却使用了列表页工具栏"
        assert "<Table" not in body and "PaginationFooter" not in body, (
            f"{relative} 声明为非列表页，却含表格或分页构件"
        )
