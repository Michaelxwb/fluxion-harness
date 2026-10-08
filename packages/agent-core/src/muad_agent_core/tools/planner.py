"""FLOW-06 的纯规划器：按原调用序把待执行的调用切成连续批次。

规则（B-06）：
- 未声明 `PARALLEL_READ`（默认 SERIAL）、未知工具、写/外部工具 → 单调用串行批，作为前后屏障；
- `PARALLEL_READ` 且资源键非空 → 可进入当前并行批；与批内已有键相同（同资源锁）→ 先冲刷，
  开新批；
- 资源键为空/None（未知依赖）→ 单调用串行批，绝不猜测；
- 并行批内部不再切分，批大小不超过 `parallel_limit`（1–16，越界/bool/小数显式拒绝）。

批次是**连续**的：一个批只在当前调用序列耗尽或遇到屏障/冲突时冲刷，不与后续调用重排。
规划器只做声明与顺序的纯计算，不执行任何 handler IO。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .registry import ToolConcurrency, ToolDefinition, ToolNotFoundError, ToolRegistry

DEFAULT_PARALLEL_LIMIT = 4
MAX_PARALLEL_LIMIT = 16


def validate_parallel_limit(value: int) -> int:
    """`parallel_limit` 必须是有界正整数（bool/小数/越界显式拒绝，不静默夹取）。"""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_PARALLEL_LIMIT:
        raise ValueError(f"parallel_limit must be an integer in 1..{MAX_PARALLEL_LIMIT}, got {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class PlannedToolCall:
    call_id: str
    tool_name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ToolBatch:
    calls: tuple[PlannedToolCall, ...]
    #: True 表示批内调用可在 `parallel_limit` 内并发；单调用批固定 False（无并发收益）。
    parallel: bool


def plan_tool_batches(
    calls: Sequence[PlannedToolCall],
    *,
    registry: ToolRegistry,
    parallel_limit: int = DEFAULT_PARALLEL_LIMIT,
) -> tuple[ToolBatch, ...]:
    limit = validate_parallel_limit(parallel_limit)
    batches: list[ToolBatch] = []
    current: list[PlannedToolCall] = []
    keys: set[str] = set()

    def flush() -> None:
        nonlocal current, keys
        if current:
            batches.append(ToolBatch(tuple(current), parallel=len(current) > 1))
            current, keys = [], set()

    for call in calls:
        key = _parallel_resource_key(call, registry)
        if key is None:
            flush()
            batches.append(ToolBatch((call,), parallel=False))
            continue
        if key in keys or len(current) >= limit:
            flush()
        current.append(call)
        keys.add(key)
    flush()
    return tuple(batches)


def _parallel_resource_key(call: PlannedToolCall, registry: ToolRegistry) -> str | None:
    """可并行键：非空表示可进并行批；None 表示未知依赖/未声明，保持串行。"""
    try:
        definition = registry.get(call.tool_name)
    except ToolNotFoundError:
        return None
    return _declared_resource_key(definition, call.arguments)


def _declared_resource_key(
    definition: ToolDefinition, arguments: Mapping[str, Any]
) -> str | None:
    if definition.concurrency is not ToolConcurrency.PARALLEL_READ or definition.resource_key is None:
        return None
    key = definition.resource_key(arguments)
    return key if isinstance(key, str) and key else None
