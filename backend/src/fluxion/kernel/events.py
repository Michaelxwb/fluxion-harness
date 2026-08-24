from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from inspect import isawaitable
from time import perf_counter
from typing import Protocol, cast


class FailPolicy(StrEnum):
    FAIL_OPEN = "fail_open"
    FAIL_CLOSED = "fail_closed"
    IGNORE = "ignore"


class HookScope(StrEnum):
    GLOBAL = "global"
    TENANT = "tenant"
    AGENT = "agent"
    USER = "user"


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


type HookHandler[PayloadT: EventPayload] = Callable[[PayloadT], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class HookRegistration[PayloadT: EventPayload]:
    registration_id: str
    event_type: type[PayloadT]
    priority: int
    timeout_ms: int | None
    fail_policy: FailPolicy
    scope: HookScope
    handler: HookHandler[PayloadT]
    scope_id: str | None = None

    def __post_init__(self) -> None:
        if not self.registration_id.strip():
            raise ValueError("registration_id is required")
        if self.timeout_ms is not None and self.timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        # 字符串值（如 "fail_closed"）统一强制转换为枚举，避免身份比较被绕过
        object.__setattr__(self, "fail_policy", FailPolicy(self.fail_policy))
        object.__setattr__(self, "scope", HookScope(self.scope))


@dataclass(frozen=True, slots=True)
class HookResult:
    registration_id: str
    event_name: str
    status: HookStatus
    latency_ms: float
    error: str | None = None


class TraceSink(Protocol):
    def emit(self, name: str, attributes: dict[str, object] | None = None) -> None: ...


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
            if result.status != HookStatus.OK and registration.fail_policy == FailPolicy.FAIL_CLOSED:
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
            if registration.fail_policy == FailPolicy.FAIL_CLOSED:
                raise HookDispatchError(registration.registration_id, result.status, str(exc)) from exc
            return result


_DEFAULT_HOOK_TIMEOUT_MS = 1000


async def _run_with_timeout[PayloadT: EventPayload](
    registration: HookRegistration[PayloadT],
    payload: PayloadT,
) -> None:
    timeout_ms = registration.timeout_ms or _DEFAULT_HOOK_TIMEOUT_MS
    if inspect.iscoroutinefunction(registration.handler):
        await asyncio.wait_for(registration.handler(payload), timeout=timeout_ms / 1000)
        return
    # 同步 handler 放到线程执行，wait_for 才能真正约束其执行时间
    result = await asyncio.wait_for(
        asyncio.to_thread(registration.handler, payload),
        timeout=timeout_ms / 1000,
    )
    if isawaitable(result):
        await result


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
