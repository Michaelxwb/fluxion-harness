"""模型执行预算层级（ADR-07）：总 deadline → 单次请求预算 → 重试预算。

   总预算 agent.deadline_ms
     └─ 单次模型请求预算 = min(上限, 剩余总预算)      # 由 deadline 派生，不再是独立默认
          └─ 重试预算 = agent.max_model_retries × 退避基数 ≤ 剩余总预算  # 总预算耗尽前停止重试

本模块是这套层级在代码里的**单一实现**：agent-core 的 `AgentRunner` 重试循环、agent-runtime 的
`ModelGateway` 恢复循环、模型 Provider 的 I/O 超时都从同一个 `ModelBudget` 取数。

- `MAX_MODEL_REQUEST_MS` 与 `RETRY_BASE_SEC` 是**算法参数**（单次请求上限、指数退避基数），
  不开放为平台设置。
- `agent` 分组四叶（`agent.max_turns` / `agent.max_tool_calls` / `agent.deadline_ms` /
  `agent.max_model_retries`）的默认值直接取 contracts 里 schema 的默认（`AgentSettings()`），
  `DEFAULT_*` 常量因此与平台设置**同源**（`AgentPolicy` 的字段默认引用它们）。
"""

from __future__ import annotations

from dataclasses import dataclass

from muad_contracts.platform_settings import AgentSettings

#: 单次模型请求上限（算法参数，不开放为设置）：请求预算先被它夹住。
MAX_MODEL_REQUEST_MS = 120_000
#: 指数退避基数（算法参数，不开放为设置）：两次重试循环共用的唯一基数。
RETRY_BASE_SEC = 0.1

_AGENT_DEFAULTS = AgentSettings()
#: 单次执行轮次上限默认（等价平台设置 `agent.max_turns` 的 schema 默认）。
DEFAULT_MAX_TURNS = _AGENT_DEFAULTS.max_turns
#: 单次执行工具调用上限默认（等价平台设置 `agent.max_tool_calls` 的 schema 默认）。
DEFAULT_MAX_TOOL_CALLS = _AGENT_DEFAULTS.max_tool_calls
#: 总预算默认（等价平台设置 `agent.deadline_ms` 的 schema 默认）。
DEFAULT_DEADLINE_MS = _AGENT_DEFAULTS.deadline_ms
#: 重试次数默认（等价平台设置 `agent.max_model_retries` 的 schema 默认）。
DEFAULT_MAX_MODEL_RETRIES = _AGENT_DEFAULTS.max_model_retries


@dataclass(frozen=True, slots=True)
class ModelBudget:
    """一次模型执行的总预算，以及由它派生的单次请求预算与重试预算。"""

    deadline_ms: int = DEFAULT_DEADLINE_MS
    max_model_retries: int = DEFAULT_MAX_MODEL_RETRIES

    def remaining_ms(self, elapsed_ms: float = 0.0) -> int:
        """总预算的剩余量，夹到 [0, deadline_ms]。"""
        remaining = self.deadline_ms - int(max(elapsed_ms, 0.0))
        return max(0, min(self.deadline_ms, remaining))

    def request_timeout_ms(self, elapsed_ms: float = 0.0) -> int:
        """单次模型请求预算 = min(上限, 剩余总预算)。"""
        return min(MAX_MODEL_REQUEST_MS, self.remaining_ms(elapsed_ms))

    def request_timeout_sec(self, elapsed_ms: float = 0.0) -> float:
        return self.request_timeout_ms(elapsed_ms) / 1000.0

    def retry_budget_ms(self) -> int:
        """重试预算 = 重试次数 × 退避基数，且不得超过总预算。"""
        return min(self.max_model_retries * int(RETRY_BASE_SEC * 1000), self.deadline_ms)

    def retry_delay_sec(self, attempt: int, retry_after: float | None = None) -> float:
        """第 `attempt`（0 基）次重试的退避秒数：429 的 Retry-After 优先，否则指数退避。"""
        return retry_after if retry_after is not None else RETRY_BASE_SEC * (2**attempt)

    def fits_before_deadline(self, *, elapsed_ms: float, delay_sec: float) -> bool:
        """再等 `delay_sec` 是否仍在总预算之内（边界取严：等于 deadline 即视为耗尽）。"""
        return elapsed_ms + delay_sec * 1000 < self.deadline_ms

    def allows_retry(
        self, *, attempt: int, elapsed_ms: float, retry_after: float | None = None
    ) -> bool:
        """是否允许第 `attempt`（0 基）次重试：次数未耗尽，且退避后仍在总预算内。"""
        if attempt >= self.max_model_retries:
            return False
        return self.fits_before_deadline(
            elapsed_ms=elapsed_ms, delay_sec=self.retry_delay_sec(attempt, retry_after)
        )


__all__ = [
    "DEFAULT_DEADLINE_MS",
    "DEFAULT_MAX_MODEL_RETRIES",
    "DEFAULT_MAX_TOOL_CALLS",
    "DEFAULT_MAX_TURNS",
    "MAX_MODEL_REQUEST_MS",
    "RETRY_BASE_SEC",
    "ModelBudget",
]
