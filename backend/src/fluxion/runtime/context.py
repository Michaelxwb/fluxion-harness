from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING
from uuid import uuid4

from fluxion.resources import ExecutionSnapshot, ResourceBinding

if TYPE_CHECKING:
    from fluxion.runtime.tools import ToolRuntime


def _new_id() -> str:
    return uuid4().hex


@dataclass(frozen=True, slots=True)
class RequestContext:
    tenant_id: str
    user_id: str
    runtime_profile_id: str
    session_id: str
    runtime_profile_version_selector: str = "latest-published"
    # TASK-A104 后 persona/model/capability 产品语义在 AgentDefinition 上；
    # 显式指定优先，缺省回退与 runtime_profile_id 同名的 AGENT_DEFINITION
    # （迁移产物即为同名——TASK-008 的 agent_id 路由会取代该回退）。
    agent_definition_id: str | None = None
    agent_definition_version_selector: str = "latest-published"
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
    # A18/ADR-005：MCP bindings + config（含已解算 credential）每执行期首次解析
    # 后缓存于此，call_tool/prepare 不再每调用重查 store.list_bindings /
    # store.get / secret resolve（N+1）。值不随执行期变化（执行期版本锚定），
    # 随 context 生命周期释放，无跨执行泄漏。
    mcp_bindings_cache: dict[str, ResourceBinding] | None = None
    mcp_configs_cache: dict[str, object] | None = None

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
