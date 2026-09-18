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


def test_console_shell_has_no_system_settings_entry() -> None:
    source = _menu_source()
    assert "系统设置" not in source
    assert "setting" not in source.lower()


def test_menu_keys_are_translated_in_both_locales() -> None:
    for locale in ("zh-CN", "en-US"):
        payload = json.loads((FRONTEND / f"locales/{locale}.json").read_text(encoding="utf-8"))
        missing = [key for key in EXPECTED_KEYS if not payload.get(key)]
        assert missing == [], f"{locale} missing menu keys: {missing}"


def test_layout_renders_menu_items_with_role_filter() -> None:
    source = (FRONTEND / "layout/AppLayout.tsx").read_text(encoding="utf-8")
    assert "menuItems" in source
    assert "adminOnly" in source
