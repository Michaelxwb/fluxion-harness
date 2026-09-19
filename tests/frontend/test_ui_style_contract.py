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
    assert "ConfigProvider" in main
    assert "locale/source/zh_CN" in main
    assert "'./styles/app.css'" in main
    assert "'@douyinfe/semi-ui/dist/css/semi.min.css'" in main

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


def test_module_list_pages_use_remote_table() -> None:
    list_pages = sorted((SRC / "modules").rglob("*Page.tsx"))
    assert list_pages, "至少应存在一个模块列表页"
    offenders = [
        str(path.relative_to(ROOT))
        for path in list_pages
        if "RemoteTable" not in _read(path)
    ]
    assert offenders == [], "模块列表页必须使用 RemoteTable（内建 PaginationFooter）: " + ", ".join(offenders)
