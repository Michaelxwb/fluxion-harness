"""[B-204][RULE-ui-001] 概览页容器与 KPI 卡片源码契约（设计 §3.3/§3.3.1/§3.6）。

页面骨架与复用：`PageHeader`（标题+说明）→ `PageSection`；KPI 卡片复用公共 `MetricCards`，
不自造卡片壳；4 个 KPI 的标题即跳转入口（设计 §3.3.1 明确指向 /agents、/skills、/tasks、
/schedules）；容器只经 hook 取数（`useOverview` → service），不按实体循环拉取。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/overview-dashboard"
PAGE = MODULE / "pages/OverviewPage.tsx"
KPIS = MODULE / "components/KpiCards.tsx"
HOOK = MODULE / "hooks/useOverview.ts"

CJK = re.compile(r"[　-〿一-鿿！-～]")

# 设计 §2.2 的 4 个 KPI → i18n 键 → 跳转目标（§3.3.1）
KPI_BINDINGS = (
    ("overview.kpi.enabledAgents", "/agents"),
    ("overview.kpi.enabledSkills", "/skills"),
    ("overview.kpi.activeTasks", "/tasks"),
    ("overview.kpi.activeSchedules", "/schedules"),
)


def _source(path: Path) -> str:
    assert path.exists(), f"缺少前端模块文件：{path}"
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def _string_literals(source: str) -> list[str]:
    groups = re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"|`([^`\n]*)`", _strip_comments(source))
    return [text for group in groups for text in group if text]


def test_page_uses_documented_skeleton() -> None:
    """[RULE-ui-001] 页面骨架：PageHeader → KPI 区 → 图表区（两个图表卡各自是 PageSection）。"""
    page = _source(PAGE)
    assert "PageHeader" in page and "PageSection" in page
    # 视觉次序：页头 → KPI 卡 → 图表区（列表已移除，图表区有自己的栅格容器）
    assert page.index("<PageHeader") < page.index("<KpiCards") < page.index("overview-charts")
    assert page.count("<PageSection") == 3, "KPI 一节 + 两块图表卡"
    assert "AppLayout" not in page, "壳层由路由承载，页面不得重复套壳"
    # 只渲染一个标题块（不重复页签标题/说明块）
    assert _strip_comments(page).count("<PageHeader") == 1


def test_kpi_cards_reuse_shared_metric_cards() -> None:
    """[设计 §3.3 CMP-02] KPI 卡片复用公共 MetricCards，不自造卡片壳。"""
    cards = _source(KPIS)
    assert "from '../../../components/common/MetricCards'" in cards
    assert "<MetricCards" in cards
    assert "metric-card" not in cards, "不得自行拼装卡片 DOM，应复用公共样式/组件"


def test_kpi_cards_cover_design_kpis_and_link_targets() -> None:
    """[设计 §2.2/§3.3.1] 4 个 KPI 齐备，标题为跳转入口且目标与设计一致。"""
    cards = _source(KPIS)
    for key, target in KPI_BINDINGS:
        assert f"t('{key}')" in cards, f"KpiCards 缺 KPI 词条 {key}"
        assert f"to=\"{target}\"" in cards, f"{key} 的跳转目标应为 {target}"
    # 字面量键（非模板串），使 scripts/check_frontend_i18n.py 能校验其已定义
    assert "t(`" not in cards and "${" not in cards, "KPI 词条须用字面量键，勿用模板串动态拼接"


def test_loading_and_error_states_do_not_fabricate_values() -> None:
    """[设计 §3.6] loading 用 Skeleton；首载失败 ErrorState + 重试（不伪造 0）。"""
    cards = _source(KPIS)
    page = _source(PAGE)
    assert "Skeleton" in cards, "loading 态须用 Skeleton 占位"
    assert "<ErrorState" in page and "onRetry={overview.reload}" in page, "失败须给 ErrorState 与重试"
    # 失败分支渲染 ErrorState 而非 KPI 卡片：错误态不得显示 0
    error_branch = page[page.index("overview.error && overview.data === null") :]
    branch_body = error_branch[: error_branch.index(") : (")]
    assert "<KpiCards" not in branch_body, "错误态不得渲染 KPI 卡片（避免显示伪造的 0）"


def test_hook_fetches_once_via_service() -> None:
    """[设计 §3.5] 容器只经 hook 取数：单次 `getOverview()`，无按实体循环。"""
    hook = _source(HOOK)
    assert "from '../services/overviewService'" in hook
    assert hook.count("getOverview(") == 1, "只允许一次聚合调用（按实体循环即 N+1）"
    assert "axios" not in hook and "fetch(" not in hook, "hook 不得绕过 service 层"
    assert "requestSeq" in hook, "须丢弃乱序响应，避免刷新与首载竞态"
    page = _source(PAGE)
    assert "useOverview()" in page, "容器须经 useOverview 取数"
    assert "overviewService" not in page, "页面不得直接调 service（取数收口在 hook）"


def test_no_hardcoded_copy_in_module_sources() -> None:
    """[RULE-front-001] 文案一律走 i18n key：三个文件的字符串字面量不得含中文。"""
    for path in (PAGE, KPIS, HOOK):
        offenders = [text for text in _string_literals(_source(path)) if CJK.search(text)]
        assert offenders == [], f"{path.name} 存在硬编码文案：{offenders}"
