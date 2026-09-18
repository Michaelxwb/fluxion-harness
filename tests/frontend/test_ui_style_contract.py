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

    css = _read(SRC / "styles/app.css")
    assert "--semi-color-primary:" in css
    assert "--app-radius:" in css
    assert "--app-shadow-card:" in css


def test_pages_use_shared_shell_and_no_raw_colors() -> None:
    page_files = sorted((SRC / "pages").glob("*.tsx")) + sorted((SRC / "modules").rglob("*.tsx"))
    assert page_files
    offenders: list[str] = []
    for path in page_files:
        source = _read(path)
        if HEX_PATTERN.search(source):
            offenders.append(f"{path.relative_to(ROOT)}: raw hex color")
    assert offenders == [], "颜色必须走 styles/app.css 的 token: " + ", ".join(offenders)

    for name in ("pages/PlaceholderPage.tsx", "pages/AgentsPage.tsx", "modules/user-identity/UserPage.tsx"):
        source = _read(SRC / name)
        assert "PageCard" in source, f"{name} 必须使用公共 PageCard"


def test_branding_has_no_legacy_name() -> None:
    for locale in ("zh-CN", "en-US"):
        payload = json.loads(_read(SRC / f"locales/{locale}.json"))
        assert payload["app.title"].startswith("Fluxion")
    index = _read(FRONTEND / "index.html")
    assert "Fluxion Console" in index
    assert "MSS" not in index


def test_semi_components_are_the_only_ui_library() -> None:
    package = json.loads(_read(FRONTEND / "package.json"))
    dependencies = package["dependencies"]
    assert "@douyinfe/semi-ui" in dependencies
    banned = ("antd", "@mui/material", "element-plus", "@chakra-ui/react")
    assert not [name for name in banned if name in dependencies]
