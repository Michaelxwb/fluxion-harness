from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend/src"
EXPECTED_KEYS = [
    "nav.overview",
    "nav.agent",
    "nav.skill",
    "nav.mcp",
    "nav.model",
    "nav.user",
    "nav.platform",
    "nav.task",
    "nav.schedule",
    "nav.audit",
]


def _menu_source() -> str:
    return (FRONTEND / "config/menu.ts").read_text(encoding="utf-8")


def test_console_shell_menu_is_the_fixed_ten_items() -> None:
    keys = re.findall(r"key:\s*'([^']+)'", _menu_source())
    assert keys == EXPECTED_KEYS

    # adminOnly 语义：恰有一项（/users），所以 ADMIN 10 项、非 ADMIN 9 项
    source = _menu_source()
    assert source.count("adminOnly: true") == 1
    admin_only_paths = re.findall(
        r"\{\s*path:\s*'([^']+)',\s*key:\s*'[^']+',\s*adminOnly:\s*true\s*\}", source
    )
    assert admin_only_paths == ["/users"], "唯一 adminOnly 项必须是 /users"
    assert len(keys) - source.count("adminOnly: true") == 9, "非 ADMIN 可见 9 项"


def test_console_shell_has_no_system_settings_entry() -> None:
    # 菜单来源与渲染处都要检查：只在 menu.ts 里 grep 挡不住在 AppLayout 里硬编码第二处导航
    for name in ("config/menu.ts", "layout/AppLayout.tsx"):
        source = (FRONTEND / name).read_text(encoding="utf-8")
        assert "系统设置" not in source, f"{name} 不得出现系统设置入口"
        assert "setting" not in source.lower(), f"{name} 不得出现 settings 字样"


def test_menu_keys_are_translated_in_both_locales() -> None:
    for locale in ("zh-CN", "en-US"):
        payload = json.loads((FRONTEND / f"locales/{locale}.json").read_text(encoding="utf-8"))
        missing = [key for key in EXPECTED_KEYS if not payload.get(key)]
        assert missing == [], f"{locale} missing menu keys: {missing}"


def test_layout_renders_menu_items_with_role_filter() -> None:
    source = (FRONTEND / "layout/AppLayout.tsx").read_text(encoding="utf-8")
    assert "import { menuItems } from '../config/menu'" in source, "Nav 项必须来自 config/menu.ts"
    assert "menuItems.filter" in source
    assert "visibleItems.map" in source, "Nav items 只能由 menuItems 派生"
    assert "adminOnly" in source
    # 不得出现第二处硬编码的导航项列表（例如 items={[{ itemKey: '/x', ... }]}）
    assert "itemKey: '/" not in source, "AppLayout 不得硬编码导航项"
    assert not re.search(r"items=\[\s*\{", source), "AppLayout 不得内联导航项数组"
