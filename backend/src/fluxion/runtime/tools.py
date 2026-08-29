from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from inspect import isawaitable
from uuid import uuid4

from fluxion.observability.tracing import traced_scope
from fluxion.runtime.context import RuntimeContext


class ToolResultStatus(StrEnum):
    COMPLETED = "completed"
    STARTED = "started"
    STREAMED = "streamed"


class ToolRuntimeError(RuntimeError):
    code = "tool_runtime_error"


class ToolAuthorizationError(ToolRuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ToolNotFoundError(ToolRuntimeError):
    code = "tool_not_found"


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    tool_id: str
    capability_id: str
    name: str
    parameters_schema: dict[str, object] | None = None
    external_dependency: bool = True
    credential_ref: str | None = None
    risk_level: str = "low"

    def __post_init__(self) -> None:
        if not self.tool_id.strip():
            raise ValueError("tool_id is required")
        if not self.capability_id.strip():
            raise ValueError("capability_id is required")


@dataclass(frozen=True, slots=True)
class ToolResult:
    status: ToolResultStatus
    result: dict[str, object] | None = None
    run_id: str | None = None
    started_status: str | None = None
    events: list[dict[str, object]] | None = None
    policy_decision_id: str | None = None

    @classmethod
    def completed(cls, result: Mapping[str, object]) -> ToolResult:
        return cls(status=ToolResultStatus.COMPLETED, result=dict(result))

    @classmethod
    def started(cls, run_id: str, status: str = "started") -> ToolResult:
        return cls(status=ToolResultStatus.STARTED, run_id=run_id, started_status=status)

    @classmethod
    def streamed(cls, events: Sequence[Mapping[str, object]]) -> ToolResult:
        return cls(
            status=ToolResultStatus.STREAMED,
            events=[dict(event) for event in events],
        )

    def with_policy_decision(self, policy_decision_id: str) -> ToolResult:
        return replace(self, policy_decision_id=policy_decision_id)


ToolRawResult = ToolResult | Mapping[str, object]
ToolExecutor = Callable[[RuntimeContext, dict[str, object]], ToolRawResult | Awaitable[ToolRawResult]]


class ToolRuntime:
    def __init__(self, *, default_tool_timeout_seconds: float = 60.0) -> None:
        self._descriptors: dict[str, ToolDescriptor] = {}
        self._executors: dict[str, ToolExecutor] = {}
        # 同步工具的硬性超时上限：与 to_thread 卸载配合，防止一次阻塞
        # 调用（如 http.get 的同步 urlopen）长时间占用 worker 线程，
        # 同时让 agent loop 的 deadline 定时器能真正被调度触发。
        self._default_timeout_seconds = default_tool_timeout_seconds

    def register(self, descriptor: ToolDescriptor, executor: ToolExecutor) -> None:
        self._descriptors[descriptor.tool_id] = descriptor
        self._executors[descriptor.tool_id] = executor

    def clone_for_execution(self) -> ToolRuntime:
        # F4：per-execution 副本。base（builtin/注入工具）的 descriptor+executor
        # 引用拷贝过来，MCP prepare 再往副本注入——执行期 MCP descriptor（含
        # credential_ref）跨租户隔离、不累积、disable 后不 stale。descriptor 是
        # frozen dataclass、executor 是无状态 callable，共享引用安全。
        clone = ToolRuntime(default_tool_timeout_seconds=self._default_timeout_seconds)
        clone._descriptors.update(self._descriptors)
        clone._executors.update(self._executors)
        return clone

    def descriptor(self, tool_id: str) -> ToolDescriptor:
        descriptor = self._descriptors.get(tool_id)
        if descriptor is None:
            raise ToolNotFoundError(f"tool {tool_id} not found")
        return descriptor

    def list_effective_descriptors(
        self,
        *,
        user_grants: set[str],
        agent_allowlist: set[str],
        tenant_policy: set[str],
    ) -> list[ToolDescriptor]:
        allowed = user_grants.intersection(agent_allowlist).intersection(tenant_policy)
        return [
            descriptor
            for tool_id, descriptor in self._descriptors.items()
            if tool_id in allowed
        ]

    async def call(
        self,
        context: RuntimeContext,
        tool_id: str,
        arguments: Mapping[str, object],
        *,
        user_grants: set[str],
        agent_allowlist: set[str],
        tenant_policy: set[str],
    ) -> ToolResult:
        # O504（TASK-008）：Tool span 经 traced_scope（tool 入 attributes，参数脱敏）
        async with traced_scope(
            "tool.call",
            attributes={
                "fluxion.tool_id": tool_id,
                "arguments": dict(arguments),
            },
        ):
            return await self._call(
                context,
                tool_id,
                arguments,
                user_grants=user_grants,
                agent_allowlist=agent_allowlist,
                tenant_policy=tenant_policy,
            )

    async def _call(
        self,
        context: RuntimeContext,
        tool_id: str,
        arguments: Mapping[str, object],
        *,
        user_grants: set[str],
        agent_allowlist: set[str],
        tenant_policy: set[str],
    ) -> ToolResult:
        descriptor = self.descriptor(tool_id)
        decision_id = uuid4().hex
        self._record_policy_decision(
            context,
            descriptor,
            decision_id,
            tool_id in user_grants and tool_id in agent_allowlist and tool_id in tenant_policy,
        )
        if tool_id not in user_grants or tool_id not in agent_allowlist or tool_id not in tenant_policy:
            raise ToolAuthorizationError("tool_not_allowed", f"tool {tool_id} is not allowed")
        raw_result = await self._execute(context, tool_id, dict(arguments))
        result = _normalize_result(raw_result).with_policy_decision(decision_id)
        context.emit(
            f"tool.{result.status.value}",
            {
                "tool_id": tool_id,
                "capability_id": descriptor.capability_id,
                "policy_decision_id": decision_id,
            },
        )
        return result

    async def _execute(
        self,
        context: RuntimeContext,
        tool_id: str,
        arguments: dict[str, object],
    ) -> ToolRawResult:
        executor = self._executors.get(tool_id)
        if executor is None:
            raise ToolNotFoundError(f"tool {tool_id} not found")
        timeout = self._default_timeout_seconds
        if asyncio.iscoroutinefunction(executor):
            # async def 执行器：调用仅构造协程（非阻塞），在循环内 await 并带上限。
            try:
                return await asyncio.wait_for(executor(context, arguments), timeout=timeout)
            except TimeoutError as exc:
                raise ToolRuntimeError(f"tool {tool_id} timed out") from exc
        # 同步执行器（含返回 coroutine 的同步 lambda）必须离开事件循环线程：
        # 否则阻塞调用会冻结整个 Pod 上所有并发 execution，连 agent loop 的
        # deadline 定时器也无法触发。先用 to_thread 取回结果（带硬性上限），
        # 若是 awaitable 再在循环内 await（同样带上限）。
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(executor, context, arguments),
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise ToolRuntimeError(f"tool {tool_id} timed out") from exc
        if isawaitable(result):
            try:
                return await asyncio.wait_for(asyncio.ensure_future(result), timeout=timeout)
            except TimeoutError as exc:
                raise ToolRuntimeError(f"tool {tool_id} timed out") from exc
        return result

    def _record_policy_decision(
        self,
        context: RuntimeContext,
        descriptor: ToolDescriptor,
        decision_id: str,
        allowed: bool,
    ) -> None:
        context.emit(
            "tool.policy_decision",
            {
                "policy_decision_id": decision_id,
                "tool_id": descriptor.tool_id,
                "capability_id": descriptor.capability_id,
                "allowed": allowed,
            },
        )


def _normalize_result(raw_result: ToolRawResult) -> ToolResult:
    if isinstance(raw_result, ToolResult):
        return raw_result
    return ToolResult.completed(raw_result)
