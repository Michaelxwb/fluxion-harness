from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from inspect import isawaitable
from typing import Protocol
from uuid import uuid4

from fluxion.observability.tracing import traced_scope
from fluxion.runtime.context import RuntimeContext


class ToolResultStatus(StrEnum):
    COMPLETED = "completed"
    STARTED = "started"
    STREAMED = "streamed"
    PENDING_APPROVAL = "pending_approval"


class PolicyDecision(StrEnum):
    """Tool 授权/风险决策（TASK-005：bool → 四级决策，REQ-SEC-003）。"""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"
    REQUIRE_APPROVAL = "require_approval"


class ToolSemanticValidator(Protocol):
    """领域语义校验器（TASK-005 / REQ-SEC-004：Schema valid ≠ semantic valid）。

    领域侧（如「customer_id 是否属当前租户」）注册校验器；返回 DENY 则拒绝执行。
    """

    async def validate(
        self,
        context: RuntimeContext,
        descriptor: ToolDescriptor,
        arguments: Mapping[str, object],
    ) -> PolicyDecision: ...


_semantic_validators: list[ToolSemanticValidator] = []


def register_semantic_validator(validator: ToolSemanticValidator) -> None:
    """注册领域语义校验器（幂等，避免重复注册）。"""
    if validator not in _semantic_validators:
        _semantic_validators.append(validator)


class ToolRuntimeError(RuntimeError):
    code = "tool_runtime_error"


class ToolAuthorizationError(ToolRuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ToolNotFoundError(ToolRuntimeError):
    code = "tool_not_found"


class ToolApprovalRequired(ToolRuntimeError):
    """高风险/中风险工具需审批/确认，不直接执行（TASK-005 / REQ-SEC-003）。"""

    code = "tool_approval_required"


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
    def __init__(
        self,
        *,
        default_tool_timeout_seconds: float = 60.0,
        on_approval_required: Callable[[RuntimeContext, ToolDescriptor, PolicyDecision], Awaitable[object]] | None = None,
    ) -> None:
        self._descriptors: dict[str, ToolDescriptor] = {}
        self._executors: dict[str, ToolExecutor] = {}
        # durable 审批回调（TASK-005）：REQUIRE_APPROVAL 时创建 durable 审批记录；
        # 未注入则抛 ToolApprovalRequired（无审批环境 fail-closed）
        self._on_approval_required = on_approval_required
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
        allowed = (
            tool_id in user_grants
            and tool_id in agent_allowlist
            and tool_id in tenant_policy
        )
        decision = _decision_for_risk(descriptor.risk_level, allowed)
        self._record_policy_decision(context, descriptor, decision_id, decision)
        if not allowed:
            raise ToolAuthorizationError("tool_not_allowed", f"tool {tool_id} is not allowed")
        # Schema validation（TASK-005）：required 字段必须在 arguments 中（最小 JSON Schema 校验）
        _validate_required_arguments(descriptor, arguments)
        # Semantic validation（TASK-005 / REQ-SEC-004）：领域校验器拒绝则 DENY
        for validator in _semantic_validators:
            semantic_decision = await validator.validate(context, descriptor, arguments)
            if semantic_decision == PolicyDecision.DENY:
                raise ToolAuthorizationError(
                    "semantic_invalid", f"tool {tool_id} failed semantic validation"
                )
        # Approval gate（TASK-005）：高风险/中风险不直接执行，需审批/确认
        if decision in (PolicyDecision.REQUIRE_APPROVAL, PolicyDecision.REQUIRE_CONFIRMATION):
            if self._on_approval_required is not None:
                await self._on_approval_required(context, descriptor, decision)
                return ToolResult(
                    status=ToolResultStatus.PENDING_APPROVAL,
                    policy_decision_id=decision_id,
                )
            raise ToolApprovalRequired(
                f"tool {tool_id} requires approval (risk_level={descriptor.risk_level}, decision={decision.value})"
            )
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
        decision: PolicyDecision,
    ) -> None:
        context.emit(
            "tool.policy_decision",
            {
                "policy_decision_id": decision_id,
                "tool_id": descriptor.tool_id,
                "capability_id": descriptor.capability_id,
                "decision": decision.value,
                "risk_level": descriptor.risk_level,
            },
        )


def _decision_for_risk(risk_level: str, allowed: bool) -> PolicyDecision:
    """risk_level → 决策（TASK-005 / REQ-SEC-003：高风险写需审批，中风险需确认）。"""
    if not allowed:
        return PolicyDecision.DENY
    if risk_level == "high":
        return PolicyDecision.REQUIRE_APPROVAL
    if risk_level == "medium":
        return PolicyDecision.REQUIRE_CONFIRMATION
    return PolicyDecision.ALLOW


def _validate_required_arguments(
    descriptor: ToolDescriptor, arguments: Mapping[str, object]
) -> None:
    """最小 JSON Schema 校验：parameters_schema.required 字段必须在 arguments 中。"""
    schema = descriptor.parameters_schema or {}
    required = schema.get("required")
    if not isinstance(required, list):
        return
    missing = [key for key in required if key not in arguments]
    if missing:
        raise ToolRuntimeError(
            f"tool {descriptor.tool_id} missing required arguments: {missing}"
        )


def _normalize_result(raw_result: ToolRawResult) -> ToolResult:
    if isinstance(raw_result, ToolResult):
        return raw_result
    return ToolResult.completed(raw_result)
