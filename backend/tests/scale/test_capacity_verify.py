"""TASK-001（Phase 6）Capacity Profile V1 契约 + scale-test 复核（FEAT-P6-01）。

S-01 / B-01（design §2.3 契约值 + §2.5 验收条件 + RULE-P6-01 只紧不松）。

真实边界：
- 真实 PostgreSQL（fluxion_test）承载 50 tenant 的版本化资源 + Execution 写路径；
- 真实 Runtime（RuntimeApplicationService + dev.echo 本地模型，无外部 LLM）；
- 批量并发 session 构造（asyncio.Semaphore 有界并发，非逐 session 串行）；
- digest 一致性 / capability equivalence 经双独立 ContextResolver（同一 PG Store）
  按架构规则 28（同 tenant+user+agent 等价解析）断言。

V1 SLO（初始契约，实测后只紧不松，见 docs/capacity/capacity-profile-v1.md）：
- success_rate = 100%；P95 execution ≤ 1000ms（满负载口径，含本地模型；2026-08-30
  满负载实测 580.1/603.9/687.5ms 三轮校准）；
- snapshot digest cross-instance 一致率 = 100%（NFR-P6-CONSIST-01）；
- capability equivalence = 100%（NFR-P6-CONSIST-02）。

FLUXION_SCALE_FULL=1 时跑 B-01 全量 5,000 sessions；默认缩样（5 tenant × 20
sessions）保持套件可常跑。PG 不可达 skip——绝不伪造 GREEN。
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from statistics import quantiles
from urllib.parse import urlparse

import pytest

from fluxion.services.capacity_verify import (
    CapacityRunReport,
    run_capacity_verification,
)

_PG_DSN = os.environ.get(
    "FLUXION_POSTGRES_DSN",
    "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test",
)
_FULL = os.environ.get("FLUXION_SCALE_FULL") == "1"


def _pg_available() -> bool:
    parsed = urlparse(_PG_DSN)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 5432), timeout=1):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.scale,
    pytest.mark.skipif(not _pg_available(), reason="PostgreSQL（fluxion_test）不可达（S-01 真实边界）"),
]


@pytest.fixture
async def report() -> AsyncGenerator[CapacityRunReport, None]:
    """缩样 scale-test（默认 5 tenant × 20 sessions = 100 executions）。"""
    run_id = uuid.uuid4().hex[:8]
    result = await run_capacity_verification(
        registry_dsn=_PG_DSN,
        tenants=5,
        sessions_per_tenant=20,
        concurrency=20,
        run_tag=f"scale-smoke-{run_id}",
    )
    yield result


class TestS01CapacityProfile:
    async def test_s01_full_load_slo(self, report: CapacityRunReport) -> None:
        """S-01[E2E]：满负载全部 SLO 达标（成功率/P95/digest/equivalence）。"""
        assert report.total_executions == 100
        assert report.success_count == report.total_executions
        assert report.p95_execution_ms <= 1000.0, (
            f"P95 {report.p95_execution_ms:.1f}ms 超出 V1 SLO 1000ms"
        )

    async def test_s01_digest_cross_instance_consistency(
        self, report: CapacityRunReport
    ) -> None:
        """S-01[E2E]：NFR-P6-CONSIST-01——双独立 resolver digest 一致率=100%。"""
        assert report.digest_checks >= 1
        assert report.digest_consistent == report.digest_checks
        assert report.digest_consistency_rate == 1.0

    async def test_s01_capability_equivalence(
        self, report: CapacityRunReport
    ) -> None:
        """S-01[E2E]：NFR-P6-CONSIST-02——同 tenant+user+agent 跨实例解析等价=100%。"""
        assert report.equivalence_checks >= 1
        assert report.equivalent_resolutions == report.equivalence_checks
        assert report.equivalence_rate == 1.0


class TestB01FullLoad:
    @pytest.mark.skipif(not _FULL, reason="FLUXION_SCALE_FULL=1 未设置（B-01 全量 5000 sessions 门控）")
    async def test_b01_five_thousand_sessions(self) -> None:
        """B-01[E2E]：V1 契约满负载（50 tenant × 100 sessions = 5,000）SLO 仍达标。

        未达标时不伪造 GREEN：断言失败即触发「只紧不松」评审（RULE-P6-01），
        实测值与瓶颈记录进 docs/capacity/capacity-profile-v1.md。
        """
        run_id = uuid.uuid4().hex[:8]
        report = await run_capacity_verification(
            registry_dsn=_PG_DSN,
            tenants=50,
            sessions_per_tenant=100,
            concurrency=100,
            run_tag=f"scale-full-{run_id}",
        )
        assert report.total_executions == 5_000
        assert report.success_count == report.total_executions, (
            f"失败 {report.total_executions - report.success_count} 个 execution"
        )
        assert report.p95_execution_ms <= 1000.0, (
            f"满负载 P95 {report.p95_execution_ms:.1f}ms 超出 V1 SLO 1000ms——记录瓶颈并触发只紧不松评审"
        )
        assert report.digest_consistency_rate == 1.0
        assert report.equivalence_rate == 1.0
        # 实测值回填契约文档（人工步骤提示；满负载实测记录进 capacity-profile-v1.md）
        print(
            f"B-01 满负载实测：total={report.total_executions} "
            f"p50={report.p50_execution_ms:.1f}ms p95={report.p95_execution_ms:.1f}ms "
            f"p99={report.p99_execution_ms:.1f}ms duration={report.duration_seconds:.1f}s "
            f"throughput={report.throughput_per_sec:.1f}/s"
        )


def test_slo_thresholds_match_contract() -> None:
    """V1 契约值/SLO 与契约文档一致（review P2：解析文档，非自引用字面量）。"""
    import re as _re

    from fluxion.services.capacity_verify import V1_PROFILE

    doc = (
        Path(__file__).resolve().parents[3]
        / "docs" / "capacity" / "capacity-profile-v1.md"
    ).read_text(encoding="utf-8")

    def _doc_value(pattern: str) -> float:
        match = _re.search(pattern, doc)
        assert match is not None, f"契约文档缺阈值锚点: {pattern}"
        return float(match.group(1).replace(",", ""))

    # 7 项容量值（§1 表）
    for key, pattern in [
        ("tenants", r"tenant 数.*?\*\*([\d,]+)\*\*"),
        ("users_per_tenant", r"users/tenant.*?\*\*([\d,]+)\*\*"),
        ("concurrent_sessions", r"concurrent sessions.*?\*\*([\d,]+)\*\*"),
        ("runtime_replicas", r"Runtime replicas.*?\*\*([\d,]+)\*\*"),
        ("workflow_concurrency", r"workflow concurrency.*?\*\*([\d,]+)\*\*"),
        ("mcp_servers_per_user", r"MCP servers/user.*?\*\*([\d,]+)\*\*"),
        ("memories_per_user", r"memories/user.*?\*\*([\d,]+)\*\*"),
    ]:
        assert V1_PROFILE[key] == _doc_value(pattern), (
            f"{key}: 代码 {V1_PROFILE[key]} ≠ 契约文档 {_doc_value(pattern)}"
        )
    # SLO 阈值（§2 表）
    assert V1_PROFILE["p95_execution_ms"] == _doc_value(r"P95 execution wall latency.*?\*\*≤([\d,]+)ms\*\*")
    assert V1_PROFILE["digest_consistency_rate"] == _doc_value(r"digest cross-instance 一致率.*?=([\d]+)%") / 100
    assert V1_PROFILE["equivalence_rate"] == _doc_value(r"\| capability equivalence.*?=([\d]+)%") / 100


def test_slo_thresholds_quantiles_helper() -> None:
    """quantiles 工具冒烟（报告 P50/P95/P99 计算路径）。"""
    values = [float(i) for i in range(1, 101)]
    p95 = quantiles(values, n=100, method="inclusive")[94]
    assert 94.0 <= p95 <= 96.0
