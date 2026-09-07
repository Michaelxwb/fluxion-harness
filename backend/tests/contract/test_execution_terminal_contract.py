"""执行终态与清理契约（TASK-013 / ADR-A014）验收测试。

覆盖 B-LIFE-DESIGN-01：
- 四类终态合法；
- 重复/冲突结束有确定语义（first wins）；
- 超时与取消区分。
"""

from __future__ import annotations

import asyncio

from fluxion.services.runtime_contracts import (
    FINALIZE_BUDGET_MS,
    ExecutionTerminalState,
    first_terminal_wins,
    resolve_terminal_state,
)


def test_B_LIFE_DESIGN_01_four_terminal_states_legal() -> None:
    """B-LIFE-DESIGN-01：四类终态合法且互斥可枚举。"""
    assert {s.value for s in ExecutionTerminalState} == {
        "completed",
        "failed",
        "cancelled",
        "timed_out",
    }


def test_B_LIFE_DESIGN_01_success_maps_completed() -> None:
    """B-LIFE-DESIGN-01：无异常即 completed。"""
    assert resolve_terminal_state(None) is ExecutionTerminalState.COMPLETED


def test_B_LIFE_DESIGN_01_error_maps_failed() -> None:
    """B-LIFE-DESIGN-01：普通异常即 failed。"""
    assert (
        resolve_terminal_state(ValueError("boom")) is ExecutionTerminalState.FAILED
    )


def test_B_LIFE_DESIGN_01_cancel_and_timeout_distinguished() -> None:
    """B-LIFE-DESIGN-01：超时与取消区分（客户端取消 ≠ 模型超时）。"""
    assert (
        resolve_terminal_state(asyncio.CancelledError())
        is ExecutionTerminalState.CANCELLED
    )
    assert (
        resolve_terminal_state(TimeoutError("deadline"))
        is ExecutionTerminalState.TIMED_OUT
    )
    # asyncio 超时即 builtin TimeoutError（3.11+ 同一对象），不断言两次。
    assert asyncio.TimeoutError is TimeoutError


def test_B_LIFE_DESIGN_01_provider_timeout_codes_map_timed_out() -> None:
    """B-LIFE-DESIGN-01：模型超时类型化 code 映射为 TIMED_OUT（TASK-016 补齐）。

    ModelProviderTimeoutError / AgentLoopTimeoutError 均非 TimeoutError 子类，
    按 code 映射——超时与取消区分落到模型层。
    """
    from fluxion.plugins.contracts import ModelProviderTimeoutError
    from fluxion.runtime.agent import AgentLoopTimeoutError

    assert (
        resolve_terminal_state(ModelProviderTimeoutError("slow"))
        is ExecutionTerminalState.TIMED_OUT
    )
    assert (
        resolve_terminal_state(AgentLoopTimeoutError("deadline"))
        is ExecutionTerminalState.TIMED_OUT
    )


def test_B_LIFE_DESIGN_01_repeat_and_conflict_deterministic() -> None:
    """B-LIFE-DESIGN-01：重复/冲突结束语义确定（首次业务终态获胜）。"""
    assert (
        first_terminal_wins(
            ExecutionTerminalState.COMPLETED, ExecutionTerminalState.FAILED
        )
        is ExecutionTerminalState.COMPLETED
    )
    assert (
        first_terminal_wins(
            ExecutionTerminalState.FAILED, ExecutionTerminalState.FAILED
        )
        is ExecutionTerminalState.FAILED
    )


def test_B_LIFE_DESIGN_01_finalize_budget_bounded() -> None:
    """B-LIFE-DESIGN-01：清理预算有界且为正。"""
    assert FINALIZE_BUDGET_MS > 0
