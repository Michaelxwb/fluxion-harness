from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter
from typing import Protocol, cast


class FailPolicy(StrEnum):
    FAIL_OPEN = "fail_open"
    FAIL_CLOSED = "fail_closed"


class HookStatus(StrEnum):
    OK = "ok"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class EventPayload:
    tenant_id: str
    execution_id: str
    trace_id: str


@dataclass(frozen=True, slots=True)
class BeforeToolCallPayload(EventPayload):
    tool_id: str
    arguments: dict[str, object]


# 105 P1-02（TASK-005）：固定 Hook Point payload（V1 八点位；分发点由 TASK-006 接线）。
# 全部 frozen 只读：handler 观察/审计/阻断，不得篡改执行业务（runtime_tool_ops 既有约束）。
@dataclass(frozen=True, slots=True)
class BeforeExecutionPayload(EventPayload):
    agent_id: str
    runtime_profile_id: str


@dataclass(frozen=True, slots=True)
class AfterExecutionPayload(EventPayload):
    agent_id: str
    terminal_state: str


@dataclass(frozen=True, slots=True)
class BeforeModelCallPayload(EventPayload):
    provider_id: str
    model: str | None
    # TASK-001（P1-01）：真实 Provider Attempt 可观测性；全默认，已有构造不受影响。
    round: int = 0
    attempt: int = 0
    streaming: bool = False


@dataclass(frozen=True, slots=True)
class AfterModelCallPayload(EventPayload):
    provider_id: str
    output_chars: int
    # TASK-001（P1-01）：与 Before 对齐的 attempt 身份＋真实结果。
    round: int = 0
    attempt: int = 0
    streaming: bool = False
    status: str = "ok"
    latency_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class AfterToolCallPayload(EventPayload):
    tool_id: str
    status: str


@dataclass(frozen=True, slots=True)
class OnExecutionErrorPayload(EventPayload):
    error_code: str
    error_message: str


@dataclass(frozen=True, slots=True)
class OnExecutionCancelledPayload(EventPayload):
    reason: str


type HookHandler[PayloadT: EventPayload] = Callable[[PayloadT], Awaitable[None]]


# TASK-002（P1-02）：Hook Point 失败语义矩阵。Pre Hook（业务动作尚未发生）
# 可 FAIL_CLOSED 安全阻断；Post/Terminal Hook（业务动作已发生或终态已结算）
# 强制 FAIL_OPEN——再阻断只会制造"业务已成功但返回失败"的状态错位。
_PRE_HOOK_PAYLOADS: frozenset[type[EventPayload]] = frozenset(
    {
        BeforeExecutionPayload,
        BeforeModelCallPayload,
        BeforeToolCallPayload,
    }
)
_POST_HOOK_PAYLOADS: frozenset[type[EventPayload]] = frozenset(
    {
        AfterModelCallPayload,
        AfterToolCallPayload,
        AfterExecutionPayload,
        OnExecutionErrorPayload,
        OnExecutionCancelledPayload,
    }
)


def allows_fail_closed(event_type: type[EventPayload]) -> bool:
    """该点位是否允许 FAIL_CLOSED（仅 Pre 点位）。未知新点位默认 False
    （fail-open 优先），并由验收完备性测试强制补分类。"""
    return event_type in _PRE_HOOK_PAYLOADS


def _is_fail_closed[PayloadT: EventPayload](
    registration: HookRegistration[PayloadT], payload: PayloadT
) -> bool:
    return (
        registration.fail_policy == FailPolicy.FAIL_CLOSED
        and allows_fail_closed(type(payload))
    )


@dataclass(frozen=True, slots=True)
class HookRegistration[PayloadT: EventPayload]:
    # 105 P2-03（TASK-007）：收敛五字段；scope/scope_id 已删（从未被消费）。
    registration_id: str
    event_type: type[PayloadT]
    priority: int
    timeout_ms: int | None
    fail_policy: FailPolicy
    handler: HookHandler[PayloadT]

    def __post_init__(self) -> None:
        if not self.registration_id.strip():
            raise ValueError("registration_id is required")
        if self.timeout_ms is not None and self.timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        # TASK-006（P2-02）：async-only——sync handler 注册期拒绝。wait_for 只能
        # 停止等待、不能终止已启动的后台线程；"约束执行时间"的旧语义是错的。
        if not inspect.iscoroutinefunction(self.handler):
            raise ValueError(
                f"hook {self.registration_id}: handler must be async "
                "(sync handlers are not supported)"
            )
        # 字符串值（如 "fail_closed"）统一强制转换为枚举，避免身份比较被绕过
        object.__setattr__(self, "fail_policy", FailPolicy(self.fail_policy))


@dataclass(frozen=True, slots=True)
class HookResult:
    registration_id: str
    event_name: str
    status: HookStatus
    latency_ms: float
    error: str | None = None


class TraceSink(Protocol):
    def emit(self, name: str, attributes: dict[str, object] | None = None) -> None: ...


@dataclass(frozen=True, slots=True)
class ModelCallAttempt:
    """单次真实 Provider Attempt 身份（TASK-001/P1-01）。

    由 AgentRuntime 在真实 Provider 调用边界构造；round＝Agent 轮次（流式
    最终答案恒为 1），attempt＝本轮内 failover 序号（1-based），
    streaming 区分流式/非流式调用。
    """

    provider_id: str
    model: str | None
    round: int
    attempt: int
    streaming: bool = False


@dataclass(frozen=True, slots=True)
class ModelCallResult:
    """单次真实 Provider Attempt 结果（TASK-001/P1-01）。

    status：ok（成功，含零 token 流式）/ error / timeout；
    output_chars：成功响应字符数（流式为本 attempt 累计 token 字符），失败为 0。
    """

    attempt: ModelCallAttempt
    status: str
    latency_ms: float
    output_chars: int


class ModelCallObserver(Protocol):
    """AgentRuntime 向执行层报告真实模型调用的最小稳定契约（TASK-001/P1-01）。

    Kernel 只依赖本 Protocol，不感知 Hook 总线/Plugin（RULE-fluxion-runtime-001）；
    Service 层注入 bridge 实现，把 attempt/result 转为 typed hook payload 分发。
    observer 缺省为 None 时 AgentLoop 行为零变化。
    """

    async def before_attempt(self, attempt: ModelCallAttempt) -> None: ...

    async def after_attempt(self, result: ModelCallResult) -> None: ...


class HookDispatchError(RuntimeError):
    code = "hook_dispatch_failed"

    def __init__(self, registration_id: str, status: HookStatus, message: str) -> None:
        self.registration_id = registration_id
        self.status = status
        super().__init__(f"hook {registration_id} {status.value}: {message}")


@dataclass(frozen=True, slots=True)
class _ScheduledHook:
    registration: HookRegistration[EventPayload]
    sequence: int


class HookScheduler:
    def __init__(self) -> None:
        self._registrations: list[_ScheduledHook] = []
        self._next_sequence = 0

    def register[PayloadT: EventPayload](self, registration: HookRegistration[PayloadT]) -> None:
        # TASK-002：Post/Terminal 点位拒绝 FAIL_CLOSED（fail-fast，不带病安装）。
        if registration.fail_policy == FailPolicy.FAIL_CLOSED and not allows_fail_closed(
            registration.event_type
        ):
            raise ValueError(
                f"hook {registration.registration_id}: "
                f"{registration.event_type.__name__} is a post/terminal hook point "
                "and must use FAIL_OPEN"
            )
        scheduled = _ScheduledHook(
            cast(HookRegistration[EventPayload], registration),
            self._next_sequence,
        )
        self._registrations.append(scheduled)
        self._next_sequence += 1

    def ordered[PayloadT: EventPayload](
        self,
        event_type: type[PayloadT],
    ) -> list[HookRegistration[PayloadT]]:
        selected = [
            item
            for item in self._registrations
            if item.registration.event_type is event_type
        ]
        ordered = sorted(selected, key=lambda item: (item.registration.priority, item.sequence))
        return [cast(HookRegistration[PayloadT], item.registration) for item in ordered]


class TypedEventBus:
    def __init__(self, scheduler: HookScheduler | None = None) -> None:
        self._scheduler = scheduler or HookScheduler()

    def register[PayloadT: EventPayload](self, registration: HookRegistration[PayloadT]) -> None:
        self._scheduler.register(registration)

    async def dispatch[PayloadT: EventPayload](
        self,
        payload: PayloadT,
        *,
        trace_sink: TraceSink | None = None,
    ) -> list[HookResult]:
        results: list[HookResult] = []
        for registration in self._scheduler.ordered(type(payload)):
            result = await self._execute_hook(registration, payload, trace_sink)
            # TASK-002：Post/Terminal 有效 FAIL_OPEN（纵深：注册期已拒，此处兜底）。
            if result.status != HookStatus.OK and _is_fail_closed(registration, payload):
                raise HookDispatchError(
                    registration.registration_id,
                    result.status,
                    result.error or "hook failed",
                )
            results.append(result)
        return results

    async def _execute_hook[PayloadT: EventPayload](
        self,
        registration: HookRegistration[PayloadT],
        payload: PayloadT,
        trace_sink: TraceSink | None,
    ) -> HookResult:
        started = perf_counter()
        try:
            await _run_with_timeout(registration, payload)
            result = _hook_result(registration, HookStatus.OK, started)
            _emit(trace_sink, "hook.completed", result)
            return result
        except TimeoutError:
            result = _hook_result(registration, HookStatus.TIMEOUT, started, "timeout")
            _emit(trace_sink, "hook.timeout", result)
            return result
        except Exception as exc:
            result = _hook_result(registration, HookStatus.ERROR, started, str(exc))
            _emit(trace_sink, "hook.error", result)
            if _is_fail_closed(registration, payload):
                raise HookDispatchError(registration.registration_id, result.status, str(exc)) from exc
            return result


_DEFAULT_HOOK_TIMEOUT_MS = 1000


async def _run_with_timeout[PayloadT: EventPayload](
    registration: HookRegistration[PayloadT],
    payload: PayloadT,
) -> None:
    timeout_ms = registration.timeout_ms or _DEFAULT_HOOK_TIMEOUT_MS
    # TASK-006：async-only（构造期已校验）。timeout 是 Runtime 等待上限——超时
    # 取消 handler 协程（cooperative）；总线不 spawn 线程，无后台残留。
    await asyncio.wait_for(
        registration.handler(payload), timeout=timeout_ms / 1000
    )


def _hook_result[PayloadT: EventPayload](
    registration: HookRegistration[PayloadT],
    status: HookStatus,
    started: float,
    error: str | None = None,
) -> HookResult:
    return HookResult(
        registration_id=registration.registration_id,
        event_name=registration.event_type.__name__,
        status=status,
        latency_ms=(perf_counter() - started) * 1000,
        error=error,
    )


def _emit(trace_sink: TraceSink | None, name: str, result: HookResult) -> None:
    if trace_sink is None:
        return
    trace_sink.emit(
        name,
        {
            "registration_id": result.registration_id,
            "event_name": result.event_name,
            "status": result.status.value,
            "latency_ms": result.latency_ms,
        },
    )
