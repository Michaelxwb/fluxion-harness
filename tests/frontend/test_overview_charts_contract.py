"""[B-205 v2][RULE-ui-001] 概览指标图源码契约（指标化改造后的事实）。

对齐结论（2026-10-06）：概览是**纯指标页**——KPI 卡 + 两块图表（近 7 天任务趋势柱状图、
任务状态分布环形图）；运营列表与运行关系说明卡已移除，明细去列表页与运行审计看。

- 图表只经 `echarts-for-react` 渲染；组件不做任何数据聚合（统计口径收口在后端
  `/overview/metrics`，前端是第二套统计即违约）；
- 图表在 canvas 里吃不到 CSS 变量：颜色必须经 `useChartTheme` 解析，**组件源码不得出现
  裸十六进制色值**（test_ui_style_contract 的同口径在图表组件上再钉一次）；
- 状态分布的图例用 **DOM 渲染**（带计数与 testid）：canvas 不可被 e2e/辅助技术读取；
- 服务层新增 `getOverviewMetrics`（`/overview/metrics`），映射只发生在 service。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/overview-dashboard"
PAGE = MODULE / "pages/OverviewPage.tsx"
TREND = MODULE / "components/TaskTrendChart.tsx"
DONUT = MODULE / "components/TaskStatusDonut.tsx"
THEME_HOOK = MODULE / "hooks/useChartTheme.ts"

HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def _source(path: Path) -> str:
    assert path.exists(), f"缺少前端模块文件：{path}"
    return path.read_text(encoding="utf-8")


def test_removed_list_components_are_gone() -> None:
    """列表与运行关系卡已删除，页面不再引用（防止指标页回流列表数据）。"""
    for name in ("RecentTaskList.tsx", "NextScheduleList.tsx", "RuntimeRelationCard.tsx"):
        assert not (MODULE / "components" / name).exists(), f"{name} 应已删除"
    page = _source(PAGE)
    for name in ("RecentTaskList", "NextScheduleList", "RuntimeRelationCard"):
        assert name not in page, f"页面不得再引用 {name}"


def test_charts_render_via_echarts_without_aggregating() -> None:
    """图表经 echarts-for-react 渲染；组件内不做数据聚合（不 reduce 出统计口径）。"""
    for path in (TREND, DONUT):
        source = _source(path)
        assert "echarts-for-react" in source, f"{path.name} 必须经 echarts-for-react 渲染"
        assert "ReactECharts" in source
    trend = _source(TREND)
    assert "trend.map" in trend, "趋势图只做 series 映射，不做聚合"
    assert "reduce(" not in trend, "趋势图不得在前端聚合（口径收口在 /overview/metrics）"


def test_chart_colors_come_from_theme_hook() -> None:
    """颜色经 useChartTheme 从 CSS 变量解析；组件源码无裸色值，也不残留 var() 字符串。"""
    theme = _source(THEME_HOOK)
    assert "--semi-color-primary" in theme, "主题 hook 必须读 CSS 变量"
    assert "MutationObserver" in theme, "须监听 theme-mode 切换以重渲染图表"
    for path in (TREND, DONUT):
        source = _source(path)
        assert "useChartTheme" in source, f"{path.name} 必须经 useChartTheme 取色"
        assert HEX_COLOR.search(source) is None, f"{path.name} 出现裸十六进制色值"
        assert "var(--" not in source, "canvas 吃不到 var()，必须传解析后的色值"


def test_status_legend_is_dom_rendered() -> None:
    """状态分布的图例必须是 DOM（testid 可断言），不能只有 canvas。"""
    donut = _source(DONUT)
    assert 'data-testid="overview-status-legend"' in donut
    assert "overview-status-" in donut, "图例项须带 overview-status-* testid"
    assert "EmptyState" in donut, "全量为 0 时渲染空态，不画空环"


def test_metrics_hook_and_page_wiring() -> None:
    """页面经 useOverviewMetrics 取数；图表区失败态用 ErrorState，不伪造全零图。"""
    page = _source(PAGE)
    assert "useOverviewMetrics()" in page
    assert "getOverviewMetrics" not in page, "页面不得直接调 service（取数收口在 hook）"
    assert page.count("<ErrorState") >= 2, "KPI 与图表区各自有失败态"
