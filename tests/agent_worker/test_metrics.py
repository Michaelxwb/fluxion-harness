"""指标被真实采集：每次累加都落一条结构化 metric 日志（设计 §3.5）。"""

from __future__ import annotations

import logging

import pytest
from conftest import TenantContext
from muad_agent_worker.metrics import increment, value


def test_increment_emits_structured_metric_log(caplog: pytest.LogCaptureFixture) -> None:
    before = value("test_metric_total")
    with caplog.at_level(logging.INFO, logger="muad_agent_worker.metrics"):
        increment("test_metric_total", 2)

    record = next(item for item in caplog.records if getattr(item, "metric", None) == "test_metric_total")
    assert record.amount == 2  # type: ignore[attr-defined]
    assert record.total == before + 2  # type: ignore[attr-defined]


async def test_claim_and_reclaim_increment_metrics(tenant: TenantContext) -> None:
    from datetime import UTC, datetime, timedelta

    from helpers import persist_task
    from muad_agent_worker.worker.service import WorkerLoop

    now = datetime.now(UTC)
    await persist_task(tenant)
    await persist_task(tenant, status="RUNNING", lease_owner="dead", lease_until=now - timedelta(seconds=1))
    claims, reclaims = value("task_claim_total"), value("task_reclaim_total")
    worker = WorkerLoop(tenant.session_factory, tenant.settings, instance_id="metrics")

    assert await worker._claimer.claim_one("metrics") is not None
    assert await worker.reclaim_expired(now=now) == 1

    assert value("task_claim_total") == claims + 1
    assert value("task_reclaim_total") == reclaims + 1
