"""日期范围筛选只有一个实现（日期控件统一复用）。

各列表页只允许引用 `components/common/DateRangeFilter`；页面里再写
`DatePicker type="dateRange"` 就是第二套实现，后续统一优化会漂移。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps" / "console-platform" / "frontend" / "src"
COMMON = FRONTEND / "components" / "common" / "DateRangeFilter.tsx"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_date_range_filter_is_shared() -> None:
    source = _read(COMMON)
    assert "export function DateRangeFilter" in source
    assert "triggerRender" in source
    assert 'type="dateRange"' in source

    pages = (
        FRONTEND / "modules" / "task-schedule" / "TaskPage.tsx",
        FRONTEND / "modules" / "audit-observability" / "components" / "AuditFilterBar.tsx",
    )
    for page in pages:
        text = _read(page)
        assert "DateRangeFilter" in text, f"{page} 未复用共享日期筛选"
        assert 'type="dateRange"' not in text, f"{page} 又写了一套日期范围控件"
        assert "DatePicker" not in text, f"{page} 直接引用了 DatePicker"

    task = _read(pages[0])
    assert task.index("task-filter-create") > task.index("task-filter-skill"), (
        "后台任务的时间筛选必须排在其它筛选之后"
    )
    audit = _read(pages[1])
    assert audit.index("audit-filter-time") > audit.index("audit-filter-resultStatus"), (
        "运行审计的时间筛选必须排在其它筛选之后"
    )
