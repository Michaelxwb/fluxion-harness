"""Overview architecture and visible states: one period snapshot, no fabricated KPI data."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/overview-dashboard"


def source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_page_has_one_header_and_shared_sections() -> None:
    page = source("pages/OverviewPage.tsx")
    assert page.count("<PageHeader") == 1 and "AppLayout" not in page
    for name in ("OverviewToolbar", "ResourceSummary", "KpiCards", "AttentionSummary", "OverviewCharts"):
        assert "<" + name in page
    assert "PageSection" in page and "<MetricCards" in source("components/KpiCards.tsx")


def test_six_kpis_are_present_and_unknown_values_are_explicit() -> None:
    cards = source("components/KpiCards.tsx")
    for key in ("runs", "successRate", "averageDuration", "activeUsers", "modelRequests", "tokens"):
        assert "t('overview.kpi." + key + "')" in cards
    assert "overview.notRecorded" in cards and "tokenReportedRequestCount" in cards
    assert "'—'" in cards


def test_loading_first_failure_and_refresh_failure_are_separate() -> None:
    page = source("pages/OverviewPage.tsx")
    assert "overview.error && overview.data === null" in page
    assert "overview.error && overview.data !== null" in page
    assert "<ErrorState" in page and "onRetry={overview.reload}" in page
    assert 'data-testid="overview-refresh-error"' in page
    assert "<OverviewSkeleton" in page
    assert "EMPTY_KPIS" not in page


def test_hook_guards_period_response_order_and_unmount() -> None:
    hook = source("hooks/useOverview.ts")
    assert hook.count("getOverview(") == 1
    assert "requestSeq" in hook and "selectedDays.current !== days" in hook
    assert "data?.days === days ? data : null" in hook
    assert "return () => { requestSeq.current += 1; }" in hook
    assert "previous?.days === days ? previous : null" in hook
    assert "setData(null)" not in hook, "refresh failure must keep the last successful snapshot"


def test_time_controls_and_refresh_use_shared_date_format() -> None:
    toolbar = source("components/OverviewToolbar.tsx")
    assert "DateTimeText" in toolbar and "generatedAt" in toolbar
    for value in (1, 7, 30):
        assert "value: " + str(value) in toolbar
    assert "aria-pressed" in toolbar and "IconRefresh" in toolbar
    assert "onClick={props.onRefresh}" in toolbar


def test_overview_is_a_one_screen_dashboard() -> None:
    """概览页按一屏布局：图表行吃掉剩余高度，不依赖固定 240px 画布产生滚动。"""
    page = source("pages/OverviewPage.tsx")
    assert 'className="overview-body"' in page
    css = (ROOT / "apps/console-platform/frontend/src/styles/app.css").read_text(encoding="utf-8")
    start = css.index("/* ---------- Overview:")
    next_block = css.find("/* ----------", start + 1)
    block = css[start:] if next_block == -1 else css[start:next_block]
    assert ".overview-dashboard {" in block and "height: 100%;" in block
    assert "grid-template-rows: repeat(2, minmax(0, 1fr))" in block
    assert ".overview-chart-canvas { flex: 1 1 auto;" in block
    assert "height: 240px" not in block
