"""workflow_poc 公共 fixture：各候选 PoC（TASK-003/004/002）与契约测试复用。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from fluxion.runtime.workflow import FailPolicy
from tests.workflow_poc.poc_workflow import MockRetentionGuard, TraceCorrelator


@pytest.fixture
def poc_fail_policy() -> FailPolicy:
    """PoC 默认失败策略：短 timeout、有限 retry、快速熔断。"""
    return FailPolicy(
        timeout_seconds=0.5,
        max_retries=1,
        retry_delay_seconds=0.01,
        breaker_threshold=3,
        breaker_cooldown_seconds=60.0,
    )


@pytest.fixture
def trace_correlator() -> TraceCorrelator:
    """SLO-OBS-01 trace 关联记录器：每口径执行链的事件关联断言。"""
    return TraceCorrelator()


@pytest.fixture
def retention_guard() -> AsyncGenerator[MockRetentionGuard, None]:
    """P-PIN retention mock（active_references 未实现；全真验属 ADR-SNAPSHOT-001 实现任务）。"""
    yield MockRetentionGuard()
