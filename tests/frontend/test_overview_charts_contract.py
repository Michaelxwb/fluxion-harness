"""Reporting charts share the aggregate and preserve theme, labels and missing usage."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/overview-dashboard"


def source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_charts_render_real_series_from_theme_tokens() -> None:
    for name in ("TaskTrendChart", "TaskStatusDonut", "TokenTrendChart"):
        body = source("components/" + name + ".tsx")
        assert "OverviewChart" in body
        assert "useChartTheme" in body
        assert re.search(r"#[0-9a-fA-F]{3,8}\b", body) is None
        assert "var(--" not in body
    wrapper = source("components/OverviewChart.tsx")
    assert "echarts-for-react" in wrapper and "ReactECharts" in wrapper
    # 图表由 flex/grid 分配高度，必须监听容器尺寸而不是只依赖 window resize。
    assert "ResizeObserver" in wrapper and "requestAnimationFrame" in wrapper
    theme = source("hooks/useChartTheme.ts")
    assert "MutationObserver" in theme and "--semi-color-primary" in theme


def test_trends_only_map_backend_points_and_keep_unknown_usage_gaps() -> None:
    for name in ("TaskTrendChart", "TokenTrendChart"):
        body = source("components/" + name + ".tsx")
        assert "trend.map" in body and "reduce(" not in body
    tokens = source("components/TokenTrendChart.tsx")
    assert "connectNulls: false" in tokens and "overview.tokens.missing" in tokens
    assert "point.inputTokens ?? 0" not in tokens


def test_four_cards_and_local_scope_switches_share_one_snapshot() -> None:
    page, charts = source("pages/OverviewPage.tsx"), source("components/OverviewCharts.tsx")
    assert page.count("useOverview(days)") == 1
    assert "useOverviewMetrics" not in page and not (MODULE / "hooks/useOverviewMetrics.ts").exists()
    for name in ("trend", "status", "token", "agent"):
        assert 'id="' + name + '"' in charts
    assert "data.trend[trendScope]" in charts and "data.status[statusScope]" in charts
    assert "getOverview" not in charts


def test_status_and_ranking_have_accessible_dom_data() -> None:
    donut, ranking = source("components/TaskStatusDonut.tsx"), source("components/AgentRankingChart.tsx")
    assert 'data-testid="overview-status-legend"' in donut and "overview-status-" in donut
    assert "EmptyState" in donut
    assert 'data-testid="overview-agent-ranking"' in ranking and "agent.runCount" in ranking
    assert "agent.agentName ??" in ranking and "dangerouslySetInnerHTML" not in ranking


def test_obsolete_recent_lists_remain_removed() -> None:
    for name in ("RecentTaskList", "NextScheduleList", "RuntimeRelationCard"):
        assert not (MODULE / "components" / (name + ".tsx")).exists()
        assert name not in source("pages/OverviewPage.tsx")
