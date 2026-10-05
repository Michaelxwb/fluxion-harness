"""[B-206][RULE-ui-001] 概览路由接入与 UI 状态源码契约（设计 §3.2/§3.6）。

- `/`（index 路由）挂 `OverviewPage`，取代原占位页；菜单第一项即概览（固定十一项之首）；
- 页面不重复套壳（壳层由 `<AppLayout>` 经路由承载）；跳转集中在本容器，块组件只上抛回调；
- 三态按 §3.6：loading 用 Skeleton、**首载失败**整页 `ErrorState` + 重试（不伪造 0）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "apps/console-platform/frontend/src"
APP = SRC / "App.tsx"
MENU = SRC / "config/menu.ts"
PAGE = SRC / "modules/overview-dashboard/pages/OverviewPage.tsx"

# 设计 §3.3.1 / §3.5：跳转目标
NAV_TARGETS = ("/tasks?taskId=", "/schedules?scheduleId=", "'/tasks'", "'/schedules'")


def _source(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def test_index_route_mounts_overview_page() -> None:
    """[设计 §3.2] `/` 挂概览页，且原占位页已被替换。"""
    app = _source(APP)
    assert "import { OverviewPage }" in app
    assert "<Route index element={<OverviewPage />} />" in app, "index 路由须挂 OverviewPage"
    assert 'titleKey="nav.overview"' not in app, "占位页须被替换"
    assert "PlaceholderPage" not in _strip_comments(app), "不再使用占位页后应移除其 import"


def test_menu_overview_is_the_first_item() -> None:
    """[设计 §2.3] 菜单固定十一项且概览为其首项（第 11 项「系统设置」见 TASK-010 契约）。"""
    menu = _source(MENU)
    entries = re.findall(r"\{\s*path:\s*'([^']*)'", menu)
    assert entries, "未能解析菜单项"
    assert entries[0] == "/", f"菜单第一项应为概览 `/`，实际 {entries[0]!r}"
    assert len(entries) == 11, f"菜单须固定十一项，实际 {len(entries)}"


def test_page_does_not_re_shell_and_wires_navigation() -> None:
    """[RULE-ui-001] 页面不重复套壳；跳转集中在容器且目标符合设计。"""
    page = _source(PAGE)
    assert "AppLayout" not in page, "壳层由路由承载，页面不得重复套壳"
    for target in NAV_TARGETS:
        assert target in page, f"缺少跳转目标 {target}"
    assert "useNavigate" in page, "导航须集中在容器"
    # 四个块都要挂上，且回调交给块（块自身不导航）
    for component in ("KpiCards", "RuntimeRelationCard", "RecentTaskList", "NextScheduleList"):
        assert f"<{component}" in page, f"页面未挂载 {component}"
    for callback in ("onOpenTask={openTask}", "onOpenSchedule={openSchedule}", "onViewAll="):
        assert callback in page, f"缺少回调接线 {callback}"


def test_first_load_failure_shows_page_error_state_with_retry() -> None:
    """[E-03 / 设计 §3.6] 首载失败整页 ErrorState + 重试；失败分支不渲染 KPI（不伪造 0）。"""
    page = _source(PAGE)
    assert "const firstLoadFailed = error && data === null;" in page
    assert "<ErrorState" in page and "onRetry={reload}" in page
    branch = page[page.index("firstLoadFailed ?") :]
    branch = branch[: branch.index(") : (")]
    assert "<KpiCards" not in branch, "失败分支不得渲染 KPI 卡片（避免显示伪造的 0）"
    assert "Skeleton" in _source(SRC / "modules/overview-dashboard/components/KpiCards.tsx")
