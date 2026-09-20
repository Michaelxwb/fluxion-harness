from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "apps/console-platform/frontend/src/components/common/DateTimeText.tsx"


def test_datetime_text_uses_the_console_format() -> None:
    source = COMPONENT.read_text(encoding="utf-8")
    assert "DATE_TIME_FORMAT = 'YYYY-MM-DD HH:mm:ss'" in source
    assert "padStart(2, '0')" in source
    assert "export function DateTimeText" in source


def test_datetime_text_is_not_hardcoded_in_pages_or_modules() -> None:
    # 覆盖 pages/ 与 modules/：业务模块的表格时间列才是主要使用面，只扫 pages/ 会漏掉绝大部分
    src = ROOT / "apps/console-platform/frontend/src"
    targets = sorted((src / "pages").glob("*.tsx")) + sorted((src / "modules").rglob("*.tsx"))
    assert targets
    offenders = [
        str(path.relative_to(ROOT))
        for path in targets
        if "toLocaleString(" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], "时间渲染必须走 DateTimeText: " + ", ".join(offenders)
