from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING
from uuid import uuid4

from fluxion.resources import ExecutionSnapshot, ResourceBinding

if TYPE_CHECKING:
    from fluxion.runtime.cancellation import CancellationToken
    from fluxion.runtime.model_providers import ScopedModelProviderResolver
    from fluxion.runtime.tools import ToolRuntime


def _new_request_id() -> str:
    return f"req_{uuid4().hex}"


def _new_trace_id() -> str:
    return f"trace_{uuid4().hex}"


def _new_execution_id() -> str:
    return f"exec_{uuid4().hex}"


@dataclass(frozen=True, slots=True)
class RequestContext:
    tenant_id: str
    user_id: str
    session_id: str
    runtime_profile_id: str | None = None
    runtime_profile_version_selector: str = "latest-published"
    # TASK-A104 后 persona/model/capability 产品语义在 AgentDefinition 上；
    # ADR-A010：仅显式 agent_definition_id 参与解析，同名回退已废弃。
    agent_definition_id: str | None = None
    agent_definition_version_selector: str = "latest-published"
    # TASK-006（ADR-A012）：缺省即受信入口补齐，必须带前缀——下游校验 fail-closed。
    request_id: str = field(default_factory=_new_request_id)
    trace_id: str = field(default_factory=_new_trace_id)
    execution_id: str = field(default_factory=_new_execution_id)

    def __post_init__(self) -> None:
        required = {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
        }
        for name, value in required.items():
            if not value.strip():
                raise ValueError(f"{name} is required")

    def with_new_execution(self) -> RequestContext:
        return replace(
            self,
            request_id=_new_request_id(),
            trace_id=_new_trace_id(),
            execution_id=_new_execution_id(),
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
    # A2/ADR-005：每执行期 effective tool policy 首次解析后缓存于此，tool call 期
    # 不再实时重解析 tenant policy/user binding——消除执行期版本漂移（snapshot 记录
    # 的版本与执行期授权不一致）与每个 tool call 的 N+1 查询。随 RuntimeContext
    # 生命周期释放，无跨执行泄漏；首次解析发生在 _model_tool_definitions（模型工具
    # 列表构建，执行起始），等价于"在执行起始锚定授权"。
    tool_policy: tuple[set[str], set[str], set[str]] | None = None
    # F4：per-execution ToolRuntime 副本。run/stream 起始 clone 自 service-level
    # base（builtin/注入工具保留），prepare 往副本注入 MCP descriptor——跨租户
    # MCP descriptor（含 credential_ref）不共享、执行结束随 context GC、不累积、
    # disable binding 后不 stale。仅 run/stream 执行路径内设值。
    tool_runtime: ToolRuntime | None = None
    # TASK-010：per-execution Provider Resolver。run/stream 起始用 ScopedModelProviderResolver
    # 包装 service-level registry 并叠加 store-backed provider，跨租户 provider 不共享、
    # 执行结束随 context GC、不累积、不 mutate service-level registry。
    model_provider_resolver: ScopedModelProviderResolver | None = None
    # F-01 收尾：执行期 MCP prepare 派生的 tool ids（binding 授权），供 _call_tool
    # 的 frozen 图预检并入 user/tenant 维度（避免 MCP 工具被三重交集误拒）。
    mcp_tool_ids: set[str] | None = None
    # A18/ADR-005：MCP bindings + config（含已解算 credential）每执行期首次解析
    # 后缓存于此，call_tool/prepare 不再每调用重查 store.list_bindings /
    # store.get / secret resolve（N+1）。值不随执行期变化（执行期版本锚定），
    # 随 context 生命周期释放，无跨执行泄漏。
    mcp_bindings_cache: dict[str, ResourceBinding] | None = None
    mcp_configs_cache: dict[str, object] | None = None
    # TASK-005（/stop）：协作式取消令牌。None＝未接入控制（旧路径/单测），
    # 视为永不取消；begin_execution 注入真实令牌，随执行结束释放。
    cancellation: CancellationToken | None = None

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
