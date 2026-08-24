from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from inspect import isawaitable
from uuid import uuid4

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
    def __init__(self) -> None:
        self._descriptors: dict[str, ToolDescriptor] = {}
        self._executors: dict[str, ToolExecutor] = {}

    def register(self, descriptor: ToolDescriptor, executor: ToolExecutor) -> None:
        self._descriptors[descriptor.tool_id] = descriptor
        self._executors[descriptor.tool_id] = executor

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
        result = executor(context, arguments)
        if isawaitable(result):
            return await asyncio.ensure_future(result)
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
