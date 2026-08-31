from __future__ import annotations

import json
from time import perf_counter

from fluxion.observability.tracing import traced_scope
from fluxion.plugins.contracts import ModelRequest, ModelResponse
from fluxion.plugins.model_provider import ModelProviderRegistry
from fluxion.registry import RegistryStore
from fluxion.resources import RuntimeProfile
from fluxion.runtime.agent import RuntimeStepResult
from fluxion.runtime.context import RequestContext, RuntimeContext, TraceEvent
from fluxion.runtime.memory import InMemorySessionMemoryStore, SessionMemoryStore
from fluxion.runtime.memory_sql import SQLSessionMemoryStore
from fluxion.runtime.tools import ToolResult
from fluxion.services.runtime_contracts import (
    CreateRuntimeProfileRequest,
    PluginSummary,
    RunRuntimeRequest,
    RunRuntimeResult,
    RuntimeApplicationError,
)


class DevEchoModelProvider:
    async def complete(self, request: ModelRequest) -> ModelResponse:
        # O503（TASK-008）：dev provider 同样经 traced_scope——S-04 全链路在
        # dev.echo 下也产出 model.complete span（真实 provider，非 mock）。
        async with traced_scope(
            "model.complete",
            attributes={
                "fluxion.model_provider_id": "dev.echo",
                "model": request.model or "dev",
            },
        ):
            content = request.messages[-1].content if request.messages else ""
            model = request.model or "dev"
            return ModelResponse(provider_id="dev.echo", content=f"{model}: {content}")


def _runtime_profile_spec(request: CreateRuntimeProfileRequest) -> dict[str, object]:
    # ADR-012：以 RuntimeProfile model 为单一真相源——构造即校验。TASK-A104 后
    # 只承载 mechanics，persona/model/capability 由 AgentDefinition 承载。
    profile = RuntimeProfile(
        request_timeout_ms=request.request_timeout_ms,
        max_retries=request.max_retries,
        max_rounds=request.max_rounds,
        concurrency=request.concurrency,
        memory_budget_mb=request.memory_budget_mb or 512,
        bootstrapped_from=request.bootstrapped_from,
    )
    return profile.model_dump(mode="json")


def _request_context(request: RunRuntimeRequest) -> RequestContext:
    return RequestContext(
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        runtime_profile_id=request.runtime_profile_id,
        session_id=request.session_id,
        runtime_profile_version_selector=request.runtime_profile_version_selector,
        agent_definition_id=request.agent_definition_id,
        request_id=request.request_id,
        trace_id=request.trace_id,
        execution_id=request.execution_id,
    )


def _run_result(
    request: RunRuntimeRequest,
    context: RuntimeContext,
    step_result: RuntimeStepResult,
    tool_results: tuple[dict[str, object], ...],
    latency_ms: float,
    service_instance_id: str,
) -> RunRuntimeResult:
    model_response = step_result.model_response
    provider_id = model_response.provider_id if model_response is not None else None
    return RunRuntimeResult(
        request_id=request.request_id,
        trace_id=context.snapshot.trace_id,
        execution_id=context.snapshot.execution_id,
        service_instance_id=service_instance_id,
        runtime_profile_id=context.snapshot.runtime_profile_id,
        runtime_profile_version=context.snapshot.runtime_profile_version,
        output=step_result.output,
        latency_ms=latency_ms,
        model_provider_id=provider_id,
        tool_results=tool_results,
    )


def _tool_result_payload(tool_id: str, result: ToolResult) -> dict[str, object]:
    payload: dict[str, object] = {"tool_id": tool_id, "status": result.status.value}
    if result.result is not None:
        payload["result"] = result.result
    if result.run_id is not None:
        payload["run_id"] = result.run_id
    if result.started_status is not None:
        payload["started_status"] = result.started_status
    if result.events is not None:
        payload["events"] = result.events
    if result.policy_decision_id is not None:
        payload["policy_decision_id"] = result.policy_decision_id
    return payload


def _tool_model_content(result: ToolResult) -> str:
    if result.result is not None:
        value: object = result.result
    elif result.events is not None:
        value = {"status": result.status.value, "events": result.events}
    else:
        value = {
            "status": result.status.value,
            "run_id": result.run_id,
            "started_status": result.started_status,
        }
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _last_event_attrs(events: tuple[TraceEvent, ...], name: str) -> dict[str, object] | None:
    for event in reversed(events):
        if event.name == name:
            return dict(event.attributes)
    return None


def _tool_events(events: tuple[TraceEvent, ...]) -> tuple[dict[str, object], ...]:
    names = {"tool.completed", "tool.started", "tool.streamed"}
    return tuple(dict(event.attributes) for event in events if event.name in names)


def _hook_events(events: tuple[TraceEvent, ...]) -> tuple[dict[str, object], ...]:
    return tuple(dict(event.attributes) for event in events if event.name.startswith("hook."))


def _elapsed_ms(started: float) -> float:
    return (perf_counter() - started) * 1000


def _error_code(exc: Exception) -> str:
    code = getattr(exc, "code", RuntimeApplicationError.code)
    return code if isinstance(code, str) else RuntimeApplicationError.code


def _derive_plugin_summaries(model_registry: ModelProviderRegistry) -> tuple[PluginSummary, ...]:
    return tuple(
        PluginSummary(provider_id, "model_provider", "trusted", "in_process")
        for provider_id in model_registry.provider_ids()
    )


def default_session_memory_store(store: RegistryStore) -> SessionMemoryStore:
    """从 RegistryStore 派生默认的会话记忆后端。

    当 store 暴露 SQLAlchemy engine 时使用 SQLSessionMemoryStore，把 L1/L2/summary
    持久化到共享 Registry（SQLite dev / PostgreSQL prod），保证 Runtime 无本地
    持久事实；否则回退 InMemorySessionMemoryStore（仅用于无 store 的测试夹具）。
    """
    engine = getattr(store, "engine", None)
    if engine is not None:
        return SQLSessionMemoryStore(engine)
    return InMemorySessionMemoryStore()
