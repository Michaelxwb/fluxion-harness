"""Overview uses one typed, read-only service boundary for the complete reporting period."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/overview-dashboard"


def source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_single_read_only_service_with_period_parameter() -> None:
    service = source("services/overviewService.ts")
    assert "from '../../../api/client'" in service
    assert re.findall(r"export async function (\w+)\(", service) == ["getOverview"]
    assert "'/overview'" in service and "params: { days }" in service
    for forbidden in ("/overview/metrics", "api.post", "api.put", "api.delete", "axios", "fetch("):
        assert forbidden not in service


def test_nullable_token_and_rate_contract_preserved_in_mapping() -> None:
    service, types = source("services/overviewService.ts"), source("types.ts")
    for camel, snake in (
        ("runCount", "run_count"),
        ("runSuccessRate", "run_success_rate"),
        ("totalTokens", "total_tokens"),
        ("tokenCoverage", "token_coverage"),
        ("activeUsers", "active_users"),
        ("agentRanking", "agent_ranking"),
        ("waitingInputRuns", "waiting_input_runs"),
        ("enabledMcps", "enabled_mcps"),
    ):
        assert camel in service and snake in service and camel in types
        assert snake not in types
    for field in ("inputTokens", "outputTokens", "totalTokens", "runSuccessRate", "averageRunDurationMs"):
        assert re.search(field + r": number \| null", types)
    assert "?? 0" not in service, "missing usage must not be fabricated as zero"


def test_no_obsolete_list_or_second_metric_types() -> None:
    types = source("types.ts")
    for name in (
        "OverviewData",
        "OverviewResources",
        "OverviewKpis",
        "OverviewTrendPoint",
        "OverviewTokenPoint",
    ):
        assert "export interface " + name in types
    for removed in ("RecentTaskItem", "NextScheduleItem", "OverviewMetrics"):
        assert "interface " + removed not in types
