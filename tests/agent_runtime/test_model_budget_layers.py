"""[B-02][unit] 模型执行预算层级（ADR-07）——真实预算解析函数，不 mock。

层级口径：
    总预算 agent.deadline_ms
      └─ 单次模型请求预算 = min(上限, 剩余总预算)
           └─ 重试预算 = agent.max_model_retries × 退避基数 ≤ 剩余总预算
"""

from __future__ import annotations

from muad_agent_core.agent import AgentPolicy
from muad_agent_core.model import (
    DEFAULT_DEADLINE_MS,
    DEFAULT_MAX_MODEL_RETRIES,
    MAX_MODEL_REQUEST_MS,
    RETRY_BASE_SEC,
    ModelBudget,
)
from muad_contracts.platform_settings import AgentSettings


def test_request_budget_is_clamped_to_remaining_total_budget() -> None:
    """单次预算 > 剩余总预算时被夹到剩余（下限 0）。"""
    budget = ModelBudget(deadline_ms=90_000, max_model_retries=3)

    assert budget.request_timeout_ms() == 90_000
    assert budget.request_timeout_ms(elapsed_ms=80_000) == 10_000
    assert budget.request_timeout_ms(elapsed_ms=90_000) == 0
    assert budget.request_timeout_ms(elapsed_ms=999_999) == 0


def test_request_budget_is_capped_by_the_algorithm_ceiling() -> None:
    """总预算足够大时，单次预算被算法上限夹住（不再各自取独立默认）。"""
    budget = ModelBudget(deadline_ms=10_000_000, max_model_retries=0)

    assert budget.request_timeout_ms() == MAX_MODEL_REQUEST_MS
    assert budget.request_timeout_sec() == MAX_MODEL_REQUEST_MS / 1000.0


def test_retry_budget_never_exceeds_total_budget() -> None:
    """重试预算 = 重试次数 × 退避基数，且不得超过总预算。"""
    roomy = ModelBudget(deadline_ms=120_000, max_model_retries=5)
    assert roomy.retry_budget_ms() == 5 * int(RETRY_BASE_SEC * 1000)
    assert roomy.retry_budget_ms() <= roomy.deadline_ms

    tight = ModelBudget(deadline_ms=100, max_model_retries=5)
    assert tight.retry_budget_ms() == 100
    assert tight.retry_budget_ms() <= tight.deadline_ms


def test_retries_stop_before_total_budget_is_exhausted() -> None:
    """总预算耗尽前停止重试：累计退避不得超过总预算，且用不满次数。"""

    budget = ModelBudget(deadline_ms=400, max_model_retries=5)
    delays: list[float] = []
    elapsed_ms = 0.0
    attempt = 0
    while budget.allows_retry(attempt=attempt, elapsed_ms=elapsed_ms):
        delay = budget.retry_delay_sec(attempt)
        delays.append(delay)
        elapsed_ms += delay * 1000
        attempt += 1

    assert sum(delays) * 1000 <= budget.deadline_ms
    assert len(delays) < budget.max_model_retries


def test_retry_stops_when_attempts_are_exhausted() -> None:
    """总预算充足时，重试次数上限生效。"""

    budget = ModelBudget(deadline_ms=10_000, max_model_retries=2)

    assert budget.allows_retry(attempt=0, elapsed_ms=0.0)
    assert budget.allows_retry(attempt=1, elapsed_ms=0.0)
    assert not budget.allows_retry(attempt=2, elapsed_ms=0.0)


def test_retry_after_header_overrides_the_backoff_base() -> None:
    """429 的 Retry-After 覆盖退避基数（与 AgentRunner 既有语义一致）。"""

    budget = ModelBudget(deadline_ms=10_000, max_model_retries=3)

    assert budget.retry_delay_sec(0, retry_after=0.05) == 0.05
    assert budget.retry_delay_sec(0) == RETRY_BASE_SEC
    assert budget.retry_delay_sec(1) == RETRY_BASE_SEC * 2


def test_defaults_and_policy_fields_track_the_platform_schema() -> None:
    """三处重试常量合一：策略默认值与平台设置 schema 同源，退避基数为单处算法常量。"""

    schema = AgentSettings()

    assert DEFAULT_DEADLINE_MS == schema.deadline_ms
    assert DEFAULT_MAX_MODEL_RETRIES == schema.max_model_retries

    budget = ModelBudget()
    assert (budget.deadline_ms, budget.max_model_retries) == (
        schema.deadline_ms,
        schema.max_model_retries,
    )

    policy = AgentPolicy()
    assert policy.deadline_ms == schema.deadline_ms
    assert policy.max_model_retries == schema.max_model_retries
    assert isinstance(RETRY_BASE_SEC, float)
    assert RETRY_BASE_SEC == 0.1
