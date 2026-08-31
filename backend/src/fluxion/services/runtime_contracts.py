from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from uuid import uuid4

from fluxion.runtime.resolver import LATEST_PUBLISHED


def _new_id() -> str:
    return uuid4().hex


class RuntimeApplicationError(RuntimeError):
    code = "runtime_application_error"

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


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
    # TASK-A104：显式指定执行的 AgentDefinition；缺省回退同名（迁移产物）。
    agent_definition_id: str | None = None
    request_id: str = field(default_factory=_new_id)
    trace_id: str = field(default_factory=_new_id)
    execution_id: str = field(default_factory=_new_id)
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
    return CreateRuntimeProfileRequest(
        tenant_id=tenant_id,
        runtime_profile_id=runtime_profile_id,
        version="1",
        request_timeout_ms=1_000,
    )
