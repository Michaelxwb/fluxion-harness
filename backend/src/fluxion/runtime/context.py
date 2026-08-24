from __future__ import annotations

from dataclasses import dataclass, field, replace
from uuid import uuid4

from fluxion.resources import ExecutionSnapshot


def _new_id() -> str:
    return uuid4().hex


@dataclass(frozen=True, slots=True)
class RequestContext:
    tenant_id: str
    user_id: str
    runtime_profile_id: str
    session_id: str
    runtime_profile_version_selector: str = "latest-published"
    request_id: str = field(default_factory=_new_id)
    trace_id: str = field(default_factory=_new_id)
    execution_id: str = field(default_factory=_new_id)

    def __post_init__(self) -> None:
        required = {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "runtime_profile_id": self.runtime_profile_id,
            "session_id": self.session_id,
        }
        for name, value in required.items():
            if not value.strip():
                raise ValueError(f"{name} is required")

    def with_new_execution(self) -> RequestContext:
        return replace(
            self,
            request_id=_new_id(),
            trace_id=_new_id(),
            execution_id=_new_id(),
        )


@dataclass(frozen=True, slots=True)
class TraceEvent:
    name: str
    tenant_id: str
    execution_id: str
    trace_id: str
    attributes: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeContext:
    request: RequestContext
    snapshot: ExecutionSnapshot
    trace: list[TraceEvent] = field(default_factory=list)

    def emit(self, name: str, attributes: dict[str, object] | None = None) -> None:
        self.trace.append(
            TraceEvent(
                name=name,
                tenant_id=self.snapshot.tenant_id,
                execution_id=self.snapshot.execution_id,
                trace_id=self.snapshot.trace_id,
                attributes=attributes or {},
            )
        )
