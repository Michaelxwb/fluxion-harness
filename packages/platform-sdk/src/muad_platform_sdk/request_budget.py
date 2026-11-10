"""Execution-local request budgets. No transport, database or application imports."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import unquote, urlsplit

from muad_contracts.egress_budget import (
    EffectiveEgressBudget,
    RequestLimits,
    SkillBudget,
    TargetMatch,
    TargetRule,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InvocationBudget:
    skill_key: str
    invocation_id: str
    policy: SkillBudget
    exhausted: asyncio.Event = field(default_factory=asyncio.Event, compare=False)
    _owner: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class AttemptContext:
    platform_key: str | None = None
    method: str | None = None
    path: str = field(default="/", repr=False)
    mcp_server_key: str | None = None
    rpc_method: str | None = None
    tool_name: str | None = None
    invocation: InvocationBudget | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if bool(self.platform_key) == bool(self.mcp_server_key):
            raise ValueError("exactly one request protocol is required")
        if self.platform_key is not None:
            if self.method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                raise ValueError("HTTP request method is required")
            if self.rpc_method is not None or self.tool_name is not None:
                raise ValueError("HTTP request cannot carry MCP fields")
            _segments(self.path)
        elif self.method is not None or (self.tool_name is not None and self.rpc_method != "tools/call"):
            raise ValueError("invalid MCP request context")


@dataclass(frozen=True, slots=True)
class BudgetEvent:
    phase: Literal["allowed", "blocked", "completed"]
    segment_id: str
    outcome: str
    attempt_id: str
    limit_kind: str | None = None


class LocalRateLimitError(RuntimeError):
    origin = "LOCAL_BUDGET"
    code = "LOCAL_BUDGET_EXHAUSTED"
    retryable = False

    def __init__(self, scope_kind: str, scope_id: str, rule_id: str, limit_kind: str,
                 limit_value: int, used: int, retry_after_seconds: float | None = None) -> None:
        self.scope_kind, self.scope_id, self.rule_id = scope_kind, scope_id, rule_id
        self.limit_kind, self.limit_value, self.used = limit_kind, limit_value, used
        self.segment_id: str | None = None
        self.invocation_id: str | None = None
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"LOCAL_BUDGET_{limit_kind}_EXHAUSTED")


@dataclass(slots=True)
class _Bucket:
    limits: RequestLimits
    scope_kind: str
    scope_id: str
    rule_id: str
    used: int = 0
    inflight: int = 0
    timestamps: deque[float] = field(default_factory=deque)

    def check(self, now: float) -> None:
        limits = self.limits
        if limits.window_seconds is not None:
            while self.timestamps and self.timestamps[0] <= now - limits.window_seconds:
                self.timestamps.popleft()
        if limits.max_requests is not None and self.used >= limits.max_requests:
            raise self.error("TOTAL", limits.max_requests, self.used)
        if limits.window_requests is not None and len(self.timestamps) >= limits.window_requests:
            remaining = self.timestamps[0] + (limits.window_seconds or 0) - now
            raise self.error("WINDOW", limits.window_requests, len(self.timestamps), remaining)
        if limits.max_inflight is not None and self.inflight >= limits.max_inflight:
            raise self.error("INFLIGHT", limits.max_inflight, self.inflight)

    def error(self, kind: str, value: int, used: int,
              retry_after: float | None = None) -> LocalRateLimitError:
        return LocalRateLimitError(self.scope_kind, self.scope_id, self.rule_id,
                                   kind, value, used, retry_after)

    def take(self, now: float) -> None:
        self.used += 1
        self.inflight += 1
        if self.limits.window_requests is not None:
            self.timestamps.append(now)


@dataclass(slots=True)
class BudgetLease:
    attempt_id: str
    outcome: str = "success"
    _buckets: tuple[_Bucket, ...] = field(default=(), repr=False)
    _released: bool = field(default=False, repr=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, repr=False)
    _on_release: Callable[[], None] | None = field(default=None, repr=False)

    def release(self) -> None:
        if self._released:
            return
        if self._loop is not None and asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("budget lease belongs to another event loop")
        self._released = True
        for bucket in self._buckets:
            bucket.inflight -= 1
        if self._on_release is not None:
            self._on_release()


def _segments(path: str) -> tuple[str, ...]:
    parsed = urlsplit(path)
    decoded = unquote(parsed.path)
    if any(c in decoded for c in ("\\", "\x00")) or "//" in decoded:
        raise ValueError("ambiguous request path")
    parts = tuple(decoded.split("/")[1:])
    if not decoded.startswith("/") or any(part in (".", "..") for part in parts):
        raise ValueError("invalid request path")
    return parts


def _matches(match: TargetMatch, context: AttemptContext) -> bool:
    if match.platform_key is not None:
        if match.platform_key != context.platform_key:
            return False
        if match.method is not None and match.method != context.method:
            return False
        if match.path_template is None:
            return True
        actual, template = _segments(context.path), match.path_template.split("/")[1:]
        return len(actual) == len(template) and all(
            bool(value) if part.startswith("{") else value == part
            for part, value in zip(template, actual, strict=True)
        )
    return (
        match.mcp_server_key == context.mcp_server_key
        and (match.rpc_method is None or match.rpc_method == context.rpc_method)
        and (match.tool_name is None or (
            context.rpc_method == "tools/call" and match.tool_name == context.tool_name
        ))
    )


class BudgetController:
    def __init__(self, policy: EffectiveEgressBudget, *, segment_id: str,
                 clock: Callable[[], float] = time.monotonic,
                 observer: Callable[[BudgetEvent], None] | None = None) -> None:
        self._policy = policy.model_copy(deep=True)
        self.segment_id, self._clock, self._observer = segment_id, clock, observer
        self._lock = asyncio.Lock()
        self._owner = object()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._buckets: dict[tuple[str, str, str], _Bucket] = {}
        self._invocations: dict[str, InvocationBudget] = {}
        self._used = 0
        self._leases: dict[str, BudgetLease] = {}
        self.observer_failures = 0

    @property
    def used(self) -> int:
        return self._used

    @property
    def inflight(self) -> int:
        return len(self._leases)

    def invocation(self, skill_key: str, invocation_id: str) -> InvocationBudget:
        if not skill_key or not invocation_id or invocation_id in self._invocations:
            raise ValueError("invocation identity must be nonempty and unique")
        handle = InvocationBudget(skill_key, invocation_id, self._policy.for_skill(skill_key),
                                  _owner=self._owner)
        self._invocations[invocation_id] = handle
        return handle

    def _bucket(self, scope: str, identity: str, rule_id: str, limits: RequestLimits) -> _Bucket:
        key = (scope, identity, rule_id)
        if key not in self._buckets:
            self._buckets[key] = _Bucket(limits, scope, identity, rule_id)
        return self._buckets[key]

    def _target_buckets(self, rules: tuple[TargetRule, ...], scope: str, identity: str,
                        context: AttemptContext) -> list[_Bucket]:
        return [self._bucket(scope, identity, "target:" + rule.id, rule.limits)
                for rule in rules if not rule.limits.is_unlimited and _matches(rule.match, context)]

    def _matching(self, context: AttemptContext) -> list[_Bucket]:
        result = []
        if not self._policy.execution.is_unlimited:
            result.append(self._bucket("EXECUTION", self.segment_id, "total", self._policy.execution))
        result.extend(self._target_buckets(self._policy.targets, "EXECUTION", self.segment_id, context))
        handle = context.invocation
        if handle is not None:
            if handle._owner is not self._owner or self._invocations.get(handle.invocation_id) is not handle:
                raise ValueError("invocation belongs to another controller")
            if not handle.policy.total.is_unlimited:
                result.append(self._bucket("INVOCATION", handle.invocation_id, "total", handle.policy.total))
            result.extend(self._target_buckets(
                handle.policy.targets, "INVOCATION", handle.invocation_id, context))
        return result

    async def _acquire(self, buckets: list[_Bucket], context: AttemptContext) -> BudgetLease:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RuntimeError("budget controller belongs to another event loop")
        self._loop = loop
        async with self._lock:
            now = self._clock()
            try:
                for bucket in buckets:
                    bucket.check(now)
            except LocalRateLimitError as exc:
                exc.segment_id = self.segment_id
                exc.invocation_id = context.invocation.invocation_id if context.invocation else None
                if exc.limit_kind == "TOTAL" and context.invocation is not None:
                    context.invocation.exhausted.set()
                raise
            for bucket in buckets:
                bucket.take(now)
            self._used += 1
            identity = uuid.uuid4().hex
            lease = BudgetLease(identity, _buckets=tuple(buckets), _loop=loop,
                                _on_release=lambda: self._release(identity))
            self._leases[identity] = lease
            return lease

    def _release(self, identity: str) -> None:
        self._leases.pop(identity, None)

    def _notify(self, phase: Literal["allowed", "blocked", "completed"], outcome: str,
                attempt_id: str, limit_kind: str | None = None) -> None:
        if self._observer is None:
            return
        if outcome not in ("pending", "success", "error", "cancelled", "local_limit", "proxy_rate_limited"):
            outcome = "unknown"
        try:
            self._observer(BudgetEvent(phase, self.segment_id, outcome, attempt_id, limit_kind))
        except Exception as exc:
            self.observer_failures += 1
            logger.warning("egress_budget_observer_failed", extra={"error_type": type(exc).__name__})

    @asynccontextmanager
    async def attempt(self, context: AttemptContext) -> AsyncIterator[BudgetLease]:
        buckets = self._matching(context)
        if not buckets:
            yield BudgetLease("")
            return
        try:
            lease = await self._acquire(buckets, context)
        except LocalRateLimitError as exc:
            self._notify("blocked", "local_limit", "", exc.limit_kind)
            raise
        try:
            self._notify("allowed", "pending", lease.attempt_id)
            yield lease
        except asyncio.CancelledError:
            lease.outcome = "cancelled"
            raise
        except BaseException:
            lease.outcome = "error"
            raise
        finally:
            lease.release()
            self._notify("completed", lease.outcome, lease.attempt_id)


def create_budget_controller(policy: EffectiveEgressBudget, *, segment_id: str,
                             clock: Callable[[], float] = time.monotonic,
                             observer: Callable[[BudgetEvent], None] | None = None,
                             ) -> BudgetController | None:
    if policy.is_unlimited:
        return None
    return BudgetController(policy, segment_id=segment_id, clock=clock, observer=observer)
