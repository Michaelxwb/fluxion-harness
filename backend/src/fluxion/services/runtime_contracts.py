from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4

from fluxion.runtime.resolver import LATEST_PUBLISHED


def _new_id() -> str:
    return uuid4().hex


def _new_request_id() -> str:
    return f"req_{uuid4().hex}"


def _new_trace_id() -> str:
    return f"trace_{uuid4().hex}"


def _new_execution_id() -> str:
    return f"exec_{uuid4().hex}"


# ADR-A012（TASK-004）：执行身份格式契约。受信入口之外禁止创建/替换身份。
_IDENTITY_PATTERN = re.compile(r"^(req|trace|exec)_[0-9a-f]{32}$")


class RequestIdentityError(ValueError):
    """执行身份非法（fail-closed）；API 层映射为 400 request_identity_invalid。"""

    code = "request_identity_invalid"


def _check_identity(kind: str, value: str) -> str:
    if not _IDENTITY_PATTERN.match(value) or not value.startswith(f"{kind}_"):
        raise RequestIdentityError(
            f"request_identity_invalid: 非法 {kind} 格式（须为 {kind}_<32hex>）"
        )
    return value


def _new_identity(kind: str) -> str:
    return f"{kind}_{uuid4().hex}"


@dataclass(frozen=True, slots=True)
class RunIdentity:
    """一次执行的身份三元组（ADR-A012）。仅受信入口可构造缺省值。"""

    request_id: str
    trace_id: str
    execution_id: str


def resolve_request_identity(
    header_request_id: str | None,
    header_trace_id: str | None,
    body_request_id: str | None,
    body_trace_id: str | None,
    body_execution_id: str | None,
) -> RunIdentity:
    """合并 header/body 身份并校验（ADR-A012 §3）：header 优先，冲突即失败，
    缺省仅此处补齐。内部层禁止调用本函数“补”身份——缺失传 None 即非法。"""
    if (
        header_request_id is not None
        and body_request_id is not None
        and header_request_id != body_request_id
    ):
        raise RequestIdentityError(
            "request_identity_invalid: header 与 body 的 request_id 不一致"
        )
    if (
        header_trace_id is not None
        and body_trace_id is not None
        and header_trace_id != body_trace_id
    ):
        raise RequestIdentityError(
            "request_identity_invalid: header 与 body 的 trace_id 不一致"
        )
    raw_request = header_request_id if header_request_id is not None else body_request_id
    raw_trace = header_trace_id if header_trace_id is not None else body_trace_id
    request_id = (
        _check_identity("req", raw_request) if raw_request is not None else _new_identity("req")
    )
    trace_id = (
        _check_identity("trace", raw_trace) if raw_trace is not None else _new_identity("trace")
    )
    execution_id = (
        _check_identity("exec", body_execution_id)
        if body_execution_id is not None
        else _new_identity("exec")
    )
    return RunIdentity(
        request_id=request_id, trace_id=trace_id, execution_id=execution_id
    )


def validate_run_identity(
    request_id: str, trace_id: str, execution_id: str
) -> RunIdentity:
    """内部层身份校验（ADR-A012 §1）：三 ID 必须全部合法，缺失/非法即 fail-closed。

    内部层（Resolver/Snapshot/Execution）禁止创建或替换身份；缺省只允许受信
    入口补齐。本函数不生成任何 ID——空串即非法。
    """
    return RunIdentity(
        request_id=_check_identity("req", request_id),
        trace_id=_check_identity("trace", trace_id),
        execution_id=_check_identity("exec", execution_id),
    )


class RuntimeApplicationError(RuntimeError):
    code = "runtime_application_error"

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        upstream_code: int | None = None,
        upstream_error: str | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        # 上游透传（网关回填）：上游信封整数码与 slug，本级 code 语义不变。
        self.upstream_code = upstream_code
        self.upstream_error = upstream_error
        super().__init__(message)


class ExecutionTerminalState(StrEnum):
    """执行终态四类（ADR-A014）。单次执行恰好其一，映射见 resolve_terminal_state。"""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


# ADR-A014 §6：清理预算（毫秒）。有限 shield 必须带 timeout，不得无限延长取消。
FINALIZE_BUDGET_MS: int = 5_000


def resolve_terminal_state(error: BaseException | None) -> ExecutionTerminalState:
    """异常 → 终态映射（ADR-A014 §1）。超时与取消必须区分。

    注意：asyncio.wait_for 超时抛 TimeoutError；客户端取消抛 CancelledError
   （BaseException，不进 except Exception）。调用方捕获 GeneratorExit 后
    同样映射为 CANCELLED 并重新传播。
    """
    if error is None:
        return ExecutionTerminalState.COMPLETED
    if isinstance(error, TimeoutError):
        return ExecutionTerminalState.TIMED_OUT
    if isinstance(error, (asyncio.CancelledError, GeneratorExit)):
        return ExecutionTerminalState.CANCELLED
    return ExecutionTerminalState.FAILED


def first_terminal_wins(
    first: ExecutionTerminalState, _late: ExecutionTerminalState
) -> ExecutionTerminalState:
    """重复/冲突结束语义（ADR-A014 §2）：首次业务终态获胜，后到者 no-op。
    清理失败独立记录（cleanup_error），永不改变已确定的业务终态。"""
    return first


@dataclass(frozen=True, slots=True)
class CreateRuntimeProfileRequest:
    """TASK-A104：RuntimeProfile 只承载 runtime mechanics（不再含 persona/
    model/capability 产品语义——见 RuntimeProfile spec model）。"""

    tenant_id: str
    runtime_profile_id: str
    version: str
    request_timeout_ms: int = 60_000
    max_retries: int = 1
    max_rounds: int = 8
    concurrency: int = 1
    memory_budget_mb: int | None = None
    bootstrapped_from: str | None = None
    # ADR-A010：租户默认标记（同租户至多一个 default=true 的 published 版本）。
    default: bool = False


@dataclass(frozen=True, slots=True)
class PublishRuntimeProfileRequest:
    tenant_id: str
    runtime_profile_id: str
    version: str
    notify_runtime: bool = True


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    tool_id: str
    arguments: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunRuntimeRequest:
    tenant_id: str
    user_id: str
    runtime_profile_id: str
    session_id: str
    input_message: str
    runtime_profile_version_selector: str = LATEST_PUBLISHED
    # TASK-A104/ADR-A010：显式指定执行的 AgentDefinition（主坐标）；
    # 同名回退已废弃，ContextResolver 链缺省 fail-closed。
    agent_definition_id: str | None = None
    # TASK-005（ADR-A012）：进程内入口的缺省即“受信入口补齐”，必须带前缀——
    # Gateway 会原样透传缺省值，裸 hex 会在 Runtime 入口被 fail-closed。
    request_id: str = field(default_factory=_new_request_id)
    trace_id: str = field(default_factory=_new_trace_id)
    execution_id: str = field(default_factory=_new_execution_id)
    tool_calls: Sequence[ToolCallRequest] = ()


@dataclass(frozen=True, slots=True)
class RunRuntimeResult:
    request_id: str
    trace_id: str
    execution_id: str
    service_instance_id: str
    runtime_profile_id: str
    runtime_profile_version: str
    output: str
    latency_ms: float
    model_provider_id: str | None
    tool_results: tuple[dict[str, object], ...] = ()

    def to_payload(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "execution_id": self.execution_id,
            "service_instance_id": self.service_instance_id,
            "runtime_profile_id": self.runtime_profile_id,
            "runtime_profile_version": self.runtime_profile_version,
            "output": self.output,
            "latency_ms": self.latency_ms,
            "model_provider_id": self.model_provider_id,
            "tool_results": list(self.tool_results),
        }


@dataclass(frozen=True, slots=True)
class RuntimeStreamEvent:
    event: str
    data: dict[str, object]


@dataclass(frozen=True, slots=True)
class PluginSummary:
    plugin_id: str
    plugin_type: str
    trust_level: str
    execution_mode: str

    def to_payload(self) -> dict[str, object]:
        return {
            "plugin_id": self.plugin_id,
            "plugin_type": self.plugin_type,
            "trust_level": self.trust_level,
            "execution_mode": self.execution_mode,
        }


@dataclass(frozen=True, slots=True)
class HealthResult:
    status: str
    service_instance_id: str

    def to_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "service_instance_id": self.service_instance_id,
        }


def default_runtime_profile_request(
    *,
    tenant_id: str,
    runtime_profile_id: str,
) -> CreateRuntimeProfileRequest:
    # ADR-A010：CLI `--bootstrap` 自举的 profile 标记为租户默认（无 ref 的
    # agent 经默认链解析）；同名成对隐式约定已废弃。
    # ADR-A013（TASK-010）：不隐式注入未接入字段值（request_timeout_ms 等未被
    # 执行读取，覆盖非默认值会造成"配置了但不生效"的虚假承诺）——保持默认。
    return CreateRuntimeProfileRequest(
        tenant_id=tenant_id,
        runtime_profile_id=runtime_profile_id,
        version="1",
        default=True,
    )
