"""单次模型/流式执行的 attempt 预算与结果收尾（TASK-007，runtime 内部模块）。

- AttemptBudget：累计业务 deadline 预算。只计业务耗时，Hook observer 等待
  经 add_hook_ms 显式扣除；单次执行局部持有，不存 Runtime 实例
  （RULE-fluxion-runtime-001）。
- fire_before/fire_after：attempt 对称上报的唯一构造点（流式/非流式共用），
  observer 为 None 时零开销；上报自身的等待同样扣除。
"""

from __future__ import annotations

from time import perf_counter

from fluxion.kernel.events import (
    ModelCallAttempt,
    ModelCallObserver,
    ModelCallResult,
)


class AttemptBudget:
    """累计业务 deadline（毫秒），Hook 等待不计入。"""

    def __init__(self, deadline_ms: int) -> None:
        self._deadline_ms = deadline_ms
        self._started = perf_counter()
        self._hook_ms = 0.0

    def add_hook_ms(self, elapsed_ms: float) -> None:
        self._hook_ms += elapsed_ms

    def remaining_ms(self) -> float:
        elapsed_ms = (perf_counter() - self._started) * 1000
        return self._deadline_ms - elapsed_ms + self._hook_ms

    def exhausted(self) -> bool:
        return self.remaining_ms() <= 0


async def fire_before(
    budget: AttemptBudget,
    observer: ModelCallObserver | None,
    attempt: ModelCallAttempt,
) -> None:
    if observer is None:
        return
    started = perf_counter()
    try:
        await observer.before_attempt(attempt)
    finally:
        budget.add_hook_ms((perf_counter() - started) * 1000)


async def fire_after(
    budget: AttemptBudget,
    observer: ModelCallObserver | None,
    attempt: ModelCallAttempt,
    attempt_started: float,
    *,
    status: str,
    output_chars: int,
) -> None:
    if observer is None:
        return
    started = perf_counter()
    try:
        await observer.after_attempt(
            ModelCallResult(
                attempt=attempt,
                status=status,
                latency_ms=(perf_counter() - attempt_started) * 1000,
                output_chars=output_chars,
            )
        )
    finally:
        budget.add_hook_ms((perf_counter() - started) * 1000)
