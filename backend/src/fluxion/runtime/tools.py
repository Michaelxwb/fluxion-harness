from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from inspect import isawaitable
from typing import Literal, Protocol
from uuid import uuid4

import jsonschema

from fluxion.observability.tracing import traced_scope
from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.tool_authorization import frozen_tool_policy as _frozen_tool_policy


class ToolResultStatus(StrEnum):
    COMPLETED = "completed"
    STARTED = "started"
    STREAMED = "streamed"
    PENDING_APPROVAL = "pending_approval"


def frozen_tool_policy(
    context: RuntimeContext,
    mcp_tool_ids: set[str] | None = None,
) -> tuple[set[str], set[str], set[str]]:
    """兼容入口：授权策略实现位于 tool_authorization 模块。"""
    return _frozen_tool_policy(context, mcp_tool_ids)


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


class ValidatorRegistry:
    """可注入、可版本化的语义校验器注册表（TASK-004）。

    替代 process-global `_semantic_validators`：随 ToolRuntime 注入，实例隔离、
    无跨执行泄漏；`version` 供决策链引用与审计（version/schema/semantic/risk/approval）。
    """

    def __init__(self, *, version: str = "1") -> None:
        self._validators: list[ToolSemanticValidator] = []
        self._version = version

    @property
    def version(self) -> str:
        return self._version

    def register(self, validator: ToolSemanticValidator) -> None:
        """注册校验器（幂等，避免重复注册）。"""
        if validator not in self._validators:
            self._validators.append(validator)

    def snapshot(self) -> tuple[ToolSemanticValidator, ...]:
        """不可变快照，供决策链迭代；隔离外部后续 register 的泄漏。"""
        return tuple(self._validators)


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
class IdempotencySpec:
    """幂等键声明（TASK-003）：side-effecting command 从 arguments 的 key_field
    取幂等键；同键重放命中缓存直接返回，不重复执行副作用。"""

    key_field: str

    def __post_init__(self) -> None:
        if not self.key_field.strip():
            raise ValueError("idempotency key_field is required")


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    tool_id: str
    capability_id: str
    name: str
    parameters_schema: dict[str, object] | None = None
    external_dependency: bool = True
    credential_ref: str | None = None
    risk_level: str = "low"
    # TASK-003：Tool Operation Contract——操作分类 + 副作用 + 幂等语义。
    operation: Literal["command", "query"] = "query"
    side_effect: bool = False
    idempotency: IdempotencySpec | None = None

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


@dataclass(frozen=True, slots=True)
class PolicyDecisionStep:
    """决策链单步审计（TASK-005）：stage → outcome（+ 可选 detail）。"""

    stage: str
    outcome: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class PolicyDecisionResult:
    """统一决策链结果（TASK-005）：version/schema/semantic/risk/approval 审计链。"""

    decision_id: str
    decision: PolicyDecision
    allowed: bool
    schema_error: str | None
    semantic_denied: bool
    chain: tuple[PolicyDecisionStep, ...]


class PolicyDecisionService:
    """统一策略决策入口（TASK-005 / F-05）。

    决策链：version（frozen effective 图命中）→ schema → semantic → risk → approval。
    输出结构化 ``PolicyDecisionResult``（含审计链），供 Tool/Approval/Workflow 复用
    与统一审计（结构化日志 + trace 关联，RULE-backend-logging-001 / RULE-fluxion-dfx-001）。
    """

    def __init__(self, semantic_validators: ValidatorRegistry) -> None:
        self._semantic_validators = semantic_validators

    async def decide(
        self,
        context: RuntimeContext,
        descriptor: ToolDescriptor,
        arguments: Mapping[str, object],
        *,
        allowed: bool,
    ) -> PolicyDecisionResult:
        chain: list[PolicyDecisionStep] = [PolicyDecisionStep("version", "pass" if allowed else "deny")]

        schema_error: str | None = None
        try:
            _validate_arguments(descriptor, arguments)
            chain.append(PolicyDecisionStep("schema", "pass"))
        except ToolRuntimeError as exc:
            schema_error = str(exc)
            chain.append(PolicyDecisionStep("schema", "deny", schema_error))

        semantic_denied = False
        for validator in self._semantic_validators.snapshot():
            if await validator.validate(context, descriptor, arguments) is PolicyDecision.DENY:
                semantic_denied = True
                break
        chain.append(PolicyDecisionStep("semantic", "deny" if semantic_denied else "pass"))

        decision = _decision_for_risk(descriptor.risk_level, allowed)
        chain.append(PolicyDecisionStep("risk", decision.value))

        requires_approval = decision in (
            PolicyDecision.REQUIRE_APPROVAL,
            PolicyDecision.REQUIRE_CONFIRMATION,
        )
        chain.append(PolicyDecisionStep("approval", "required" if requires_approval else "pass"))

        return PolicyDecisionResult(
            decision_id=uuid4().hex,
            decision=decision,
            allowed=allowed,
            schema_error=schema_error,
            semantic_denied=semantic_denied,
            chain=tuple(chain),
        )


class ToolRuntime:
    def __init__(
        self,
        *,
        default_tool_timeout_seconds: float = 60.0,
        on_approval_required: Callable[[RuntimeContext, ToolDescriptor, PolicyDecision], Awaitable[object]] | None = None,
        semantic_validators: ValidatorRegistry | None = None,
    ) -> None:
        self._descriptors: dict[str, ToolDescriptor] = {}
        self._executors: dict[str, ToolExecutor] = {}
        # durable 审批回调（TASK-005）：REQUIRE_APPROVAL 时创建 durable 审批记录；
        # 未注入则抛 ToolApprovalRequired（无审批环境 fail-closed）
        self._on_approval_required = on_approval_required
        # TASK-004：语义校验器经注入的 ValidatorRegistry（去 process-global 可变）。
        self._semantic_validators = semantic_validators or ValidatorRegistry()
        # TASK-005：统一策略决策入口（version/schema/semantic/risk/approval 决策链）。
        self._policy = PolicyDecisionService(self._semantic_validators)
        # TASK-003：幂等去重缓存（per-execution，clone_for_execution 不拷贝）。
        # 只缓存显式声明 idempotency 的 command 结果，同键重放不重复副作用。
        self._idempotency_cache: dict[str, ToolResult] = {}
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
        context: RuntimeContext,
        *,
        mcp_tool_ids: set[str] | None = None,
    ) -> list[ToolDescriptor]:
        user_grants, agent_allowlist, tenant_policy = frozen_tool_policy(context, mcp_tool_ids)
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
        mcp_tool_ids: set[str] | None = None,
    ) -> ToolResult:
        # O504（TASK-008）：Tool span 经 traced_scope（tool 入 attributes，参数脱敏）
        async with traced_scope(
            "tool.call",
            attributes={
                "fluxion.tool_id": tool_id,
                "arguments": dict(arguments),
            },
        ):
            return await self._call(context, tool_id, arguments, mcp_tool_ids=mcp_tool_ids)

    async def _call(
        self,
        context: RuntimeContext,
        tool_id: str,
        arguments: Mapping[str, object],
        *,
        mcp_tool_ids: set[str] | None = None,
    ) -> ToolResult:
        descriptor = self.descriptor(tool_id)
        user_grants, agent_allowlist, tenant_policy = frozen_tool_policy(context, mcp_tool_ids)
        allowed = (
            tool_id in user_grants
            and tool_id in agent_allowlist
            and tool_id in tenant_policy
        )
        # TASK-005：统一策略决策入口（version/schema/semantic/risk/approval）。
        decision = await self._policy.decide(context, descriptor, arguments, allowed=allowed)
        self._record_policy_decision(context, descriptor, decision)
        if not decision.allowed:
            raise ToolAuthorizationError("tool_not_allowed", f"tool {tool_id} is not allowed")
        # Schema validation（TASK-002）：完整 JSON Schema 校验
        if decision.schema_error is not None:
            raise ToolRuntimeError(decision.schema_error)
        # Semantic validation（TASK-005 / REQ-SEC-004）：领域校验器拒绝则 DENY
        if decision.semantic_denied:
            raise ToolAuthorizationError(
                "semantic_invalid", f"tool {tool_id} failed semantic validation"
            )
        # Approval gate（TASK-005）：高风险/中风险不直接执行，需审批/确认
        if decision.decision in (PolicyDecision.REQUIRE_APPROVAL, PolicyDecision.REQUIRE_CONFIRMATION):
            if self._on_approval_required is not None:
                await self._on_approval_required(context, descriptor, decision.decision)
                return ToolResult(
                    status=ToolResultStatus.PENDING_APPROVAL,
                    policy_decision_id=decision.decision_id,
                )
            raise ToolApprovalRequired(
                f"tool {tool_id} requires approval (risk_level={descriptor.risk_level}, decision={decision.decision.value})"
            )
        # TASK-003：幂等去重——同幂等键重放直接返回缓存结果，不重复执行副作用。
        cached = self._idempotent_result(descriptor, arguments)
        if cached is not None:
            context.emit(
                "tool.idempotent_replay",
                {"tool_id": tool_id, "capability_id": descriptor.capability_id},
            )
            return cached
        raw_result = await self._execute(context, tool_id, dict(arguments))
        result = _normalize_result(raw_result).with_policy_decision(decision.decision_id)
        self._cache_idempotent_result(descriptor, arguments, result)
        context.emit(
            f"tool.{result.status.value}",
            {
                "tool_id": tool_id,
                "capability_id": descriptor.capability_id,
                "policy_decision_id": decision.decision_id,
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
        result: PolicyDecisionResult,
    ) -> None:
        # TASK-005：统一审计决策链（version/schema/semantic/risk/approval），
        # 结构化日志 + trace 关联（context.emit 进 trace）。
        context.emit(
            "tool.policy_decision",
            {
                "policy_decision_id": result.decision_id,
                "tool_id": descriptor.tool_id,
                "capability_id": descriptor.capability_id,
                "decision": result.decision.value,
                "risk_level": descriptor.risk_level,
                "chain": [
                    {"stage": step.stage, "outcome": step.outcome, "detail": step.detail}
                    for step in result.chain
                ],
            },
        )

    def _idempotency_key(
        self, descriptor: ToolDescriptor, arguments: Mapping[str, object]
    ) -> str | None:
        """返回幂等缓存键；未声明 idempotency 或 arguments 缺键时返回 None（不去重）。"""
        spec = descriptor.idempotency
        if spec is None:
            return None
        key = arguments.get(spec.key_field)
        if key is None:
            return None
        return f"{descriptor.tool_id}:{key}"

    def _idempotent_result(
        self, descriptor: ToolDescriptor, arguments: Mapping[str, object]
    ) -> ToolResult | None:
        key = self._idempotency_key(descriptor, arguments)
        if key is None:
            return None
        return self._idempotency_cache.get(key)

    def _cache_idempotent_result(
        self, descriptor: ToolDescriptor, arguments: Mapping[str, object], result: ToolResult
    ) -> None:
        key = self._idempotency_key(descriptor, arguments)
        if key is not None:
            self._idempotency_cache[key] = result


def _decision_for_risk(risk_level: str, allowed: bool) -> PolicyDecision:
    """risk_level → 决策（TASK-005 / REQ-SEC-003：高风险写需审批，中风险需确认）。"""
    if not allowed:
        return PolicyDecision.DENY
    if risk_level == "high":
        return PolicyDecision.REQUIRE_APPROVAL
    if risk_level == "medium":
        return PolicyDecision.REQUIRE_CONFIRMATION
    return PolicyDecision.ALLOW


def _validate_arguments(
    descriptor: ToolDescriptor, arguments: Mapping[str, object]
) -> None:
    """完整 JSON Schema 校验（TASK-002 / REQ-SEC-004）。

    type/enum/required/nested/additionalProperties 全量校验，替换最小 required 校验。
    """
    schema = descriptor.parameters_schema
    if not schema:
        return
    try:
        jsonschema.validate(instance=dict(arguments), schema=schema)
    except jsonschema.ValidationError as exc:
        raise ToolRuntimeError(
            f"tool {descriptor.tool_id} invalid arguments: {exc.message}"
        ) from exc


def _normalize_result(raw_result: ToolRawResult) -> ToolResult:
    if isinstance(raw_result, ToolResult):
        return raw_result
    return ToolResult.completed(raw_result)
