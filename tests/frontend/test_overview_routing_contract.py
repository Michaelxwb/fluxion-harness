"""Overview entry and emitted filters must be consumed by the actual target pages."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/overview-dashboard"


def source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_index_route_mounts_overview_page() -> None:
    app = (MODULE.parents[1] / "App.tsx").read_text()
    assert "<Route index element={<OverviewPage />} />" in app
    menu = (MODULE.parents[1] / "config/menu.ts").read_text()
    entries = re.findall(r"\{\s*path:\s*'([^']*)'", menu)
    assert entries[0] == "/" and len(entries) == 11


def test_navigation_targets_are_real_and_user_resource_respects_role() -> None:
    resources = source("components/ResourceSummary.tsx")
    for target in ("/agents", "/skills", "/mcp", "/models", "/users", "/schedules"):
        assert "to: '" + target + "'" in resources
    assert "account?.role !== 'ADMIN'" in resources
    assert "'Agent ' + agent.agentId.slice(0, 8)" in source("components/AgentRankingChart.tsx")


def test_audit_links_have_receiver_for_model_and_agent_filters() -> None:
    page = (MODULE.parent / "audit-observability/pages/AuditPage.tsx").read_text()
    assert "searchParams.get('agentId')" in page and "searchParams.get('auditType')" in page
    assert "UUID_VALUE.test(agentId)" in page
    assert "/audits?auditType=MODEL" in source("components/KpiCards.tsx")
    assert "/audits?agentId=" in source("components/AgentRankingChart.tsx")


def test_task_status_links_are_visible_filters_in_the_target_page() -> None:
    target = (MODULE.parent / "task-schedule/TaskPage.tsx").read_text()
    assert "searchParams.get('status')" in target and "status: linkedStatus" in target
    summary = source("components/AttentionSummary.tsx")
    for status in ("QUEUED", "RUNNING", "WAITING", "FAILED"):
        assert "/tasks?status=" + status in summary
