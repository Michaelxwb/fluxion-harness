from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace

from fluxion.plugins.contracts import (
    ModelMessage,
    ModelProviderError,
    ModelProviderRegistryProtocol,
    ModelProviderTimeoutError,
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolDefinition,
)
from fluxion.resources import ExecutionSnapshot
from fluxion.runtime.context import RequestContext, RuntimeContext, TraceEvent
from fluxion.runtime.memory import MemoryManager, MemoryPolicy, MemoryRecord, SessionMemoryStore
from fluxion.runtime.resolver import ExecutionSnapshotBuilder


@dataclass(frozen=True, slots=True)
class RuntimeStepResult:
    snapshot: ExecutionSnapshot
    output: str
    trace: tuple[TraceEvent, ...]
    model_response: ModelResponse | None = None
    tool_results: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class ModelToolResult:
    call_id: str
    tool_id: str
    content: str
    payload: dict[str, object]


class AgentLoopError(ModelProviderError):
    code = "agent_loop_error"


class AgentLoopLimitError(AgentLoopError):
    code = "agent_loop_limit_exceeded"


class AgentLoopTimeoutError(AgentLoopError):
    code = "agent_loop_timeout"


class AgentLoopDuplicateToolCallError(AgentLoopError):
    code = "agent_loop_duplicate_tool_call"


ModelToolHandler = Callable[[RuntimeContext, ToolCall], Awaitable[ModelToolResult]]


class AgentRuntime:
    def __init__(
        self,
        *,
        snapshot_builder: ExecutionSnapshotBuilder,
        memory_store: SessionMemoryStore,
        memory_policy: MemoryPolicy | None = None,
        model_providers: ModelProviderRegistryProtocol | None = None,
    ) -> None:
        self._snapshot_builder = snapshot_builder
        self._memory = MemoryManager(memory_store, policy=memory_policy)
        self._model_providers = model_providers

    @property
    def memory(self) -> MemoryManager:
        return self._memory

    @property
    def local_durable_fact_count(self) -> int:
        return 0

    async def start_execution(self, request: RequestContext) -> RuntimeContext:
        snapshot = await self._snapshot_builder.build(request)
        context = RuntimeContext(request=request, snapshot=snapshot)
        context.emit(
            "execution.started",
            {
                "runtime_profile_version": snapshot.runtime_profile_version,
                "skill_versions": dict(snapshot.skill_versions),
            },
        )
        return context

    async def run_step(
        self,
        context: RuntimeContext,
        input_message: str,
        *,
        tools: Iterable[ToolDefinition] = (),
        tool_handler: ModelToolHandler | None = None,
    ) -> RuntimeStepResult:
        session_history = await self._memory.read_session_context(context)
        await self._memory.add_message(context, "user", input_message)
        context.emit("execution.step", {"input_tokens": len(input_message.split())})
        model_response, tool_results = await self._maybe_complete_model(
            context,
            input_message,
            list(tools),
            session_history=session_history,
            tool_handler=tool_handler,
        )
        output = model_response.content if model_response is not None else "ok"
        if model_response is not None and model_response.content:
            await self._memory.add_message(context, "assistant", model_response.content)
        return RuntimeStepResult(
            snapshot=context.snapshot,
            output=output,
            trace=tuple(context.trace),
            model_response=model_response,
            tool_results=tool_results,
        )

    async def finish_execution(self, context: RuntimeContext) -> None:
        await self._memory.finish_execution(context)
        context.emit("execution.finished", {})

    async def run(
        self,
        request: RequestContext,
        *,
        input_messages: Iterable[str] = (),
    ) -> RuntimeStepResult:
        context = await self.start_execution(request)
        result = RuntimeStepResult(context.snapshot, "ok", tuple(context.trace))
        try:
            for message in input_messages:
                result = await self.run_step(context, message)
            return RuntimeStepResult(
                context.snapshot,
                result.output,
                tuple(context.trace),
                result.model_response,
                result.tool_results,
            )
        finally:
            # 无论 run_step 是否抛错都关闭 execution，避免 L0 memory 会话泄漏。
            await self.finish_execution(context)

    async def _maybe_complete_model(
        self,
        context: RuntimeContext,
        input_message: str,
        tools: list[ToolDefinition],
        *,
        session_history: list[MemoryRecord],
        tool_handler: ModelToolHandler | None,
    ) -> tuple[ModelResponse | None, tuple[dict[str, object], ...]]:
        if self._model_providers is None:
            return None, ()
        provider_ids = _provider_chain(context.snapshot.model_resolution)
        if not provider_ids:
            return None, ()
        timeout_ms = _timeout_ms(context.snapshot.model_resolution)
        messages = _model_messages(context, session_history, input_message)
        try:
            return await asyncio.wait_for(
                self._run_model_loop(
                    context,
                    provider_ids=provider_ids,
                    messages=messages,
                    tools=tools,
                    timeout_ms=timeout_ms,
                    tool_handler=tool_handler,
                ),
                timeout=_deadline_ms(context.snapshot.model_resolution) / 1000,
            )
        except TimeoutError as exc:
            context.emit(
                "agent_loop.timeout",
                {"deadline_ms": _deadline_ms(context.snapshot.model_resolution)},
            )
            raise AgentLoopTimeoutError("agent loop deadline exceeded") from exc

    async def _run_model_loop(
        self,
        context: RuntimeContext,
        *,
        provider_ids: list[str],
        messages: list[ModelMessage],
        tools: list[ToolDefinition],
        timeout_ms: int,
        tool_handler: ModelToolHandler | None,
    ) -> tuple[ModelResponse, tuple[dict[str, object], ...]]:
        max_rounds = _max_rounds(context.snapshot.model_resolution)
        seen_call_ids: set[str] = set()
        seen_signatures: set[str] = set()
        tool_results: list[dict[str, object]] = []
        for round_index in range(1, max_rounds + 1):
            request = ModelRequest(
                messages=list(messages),
                tools=tools,
                timeout_ms=timeout_ms,
                model=_optional_str(context.snapshot.model_resolution.get("model")),
            )
            response = await self._complete_with_failover(
                context,
                provider_ids,
                request,
                timeout_ms,
            )
            if not response.tool_calls:
                return response, tuple(tool_results)
            if tool_handler is None:
                return response, tuple(tool_results)
            messages.append(
                ModelMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )
            for call in response.tool_calls:
                _remember_tool_call(call, seen_call_ids, seen_signatures)
                result = await tool_handler(context, call)
                tool_results.append(result.payload)
                messages.append(
                    ModelMessage(
                        role="tool",
                        content=result.content,
                        tool_call_id=result.call_id,
                        name=result.tool_id,
                    )
                )
            context.emit(
                "agent_loop.round_completed",
                {"round": round_index, "tool_call_count": len(response.tool_calls)},
            )
        context.emit("agent_loop.limit_exceeded", {"max_rounds": max_rounds})
        raise AgentLoopLimitError(f"agent loop exceeded {max_rounds} rounds")

    async def _complete_with_failover(
        self,
        context: RuntimeContext,
        provider_ids: list[str],
        request: ModelRequest,
        timeout_ms: int,
    ) -> ModelResponse:
        last_error: ModelProviderError | None = None
        for provider_id in provider_ids:
            try:
                response = await self._complete_once(
                    context,
                    provider_id,
                    request,
                    timeout_ms,
                )
                context.emit(
                    "model.completed",
                    {"provider_id": provider_id, "tool_call_count": len(response.tool_calls)},
                )
                return response
            except ModelProviderTimeoutError as exc:
                context.emit("model.timeout", {"provider_id": provider_id, "timeout_ms": timeout_ms})
                last_error = exc
            except ModelProviderError as exc:
                context.emit("model.error", {"provider_id": provider_id, "error": str(exc)})
                last_error = exc
        if last_error is None:
            raise ModelProviderError("no model provider configured")
        raise last_error

    async def _complete_once(
        self,
        context: RuntimeContext,
        provider_id: str,
        request: ModelRequest,
        timeout_ms: int,
    ) -> ModelResponse:
        if self._model_providers is None:
            raise ModelProviderError("model provider registry is not configured")
        provider = self._model_providers.resolve(provider_id)
        scoped_request = replace(
            request,
            tenant_id=context.snapshot.tenant_id,
            user_id=context.snapshot.user_id,
            provider_version=context.snapshot.plugin_versions.get(provider_id),
        )
        try:
            return await _wait_for_provider(provider.complete(scoped_request), timeout_ms)
        except TimeoutError as exc:
            raise ModelProviderTimeoutError(f"model provider {provider_id} timed out") from exc
        except ModelProviderError:
            raise
        except Exception as exc:
            # Provider 边界：插件抛出的非预期异常（如 JSONDecodeError）也纳入 failover，
            # 不因单个 provider 的实现缺陷终止整个 execution。
            raise ModelProviderError(
                f"model provider {provider_id} failed: {exc}"
            ) from exc


async def _wait_for_provider(
    awaitable: Awaitable[ModelResponse],
    timeout_ms: int,
) -> ModelResponse:
    return await asyncio.wait_for(awaitable, timeout=timeout_ms / 1000)


def _provider_chain(model_resolution: dict[str, object]) -> list[str]:
    provider = _optional_str(model_resolution.get("provider"))
    failover = model_resolution.get("failover")
    chain = [provider] if provider is not None else []
    if isinstance(failover, list):
        chain.extend(item for item in failover if isinstance(item, str) and item.strip())
    return list(dict.fromkeys(chain))


def _timeout_ms(model_resolution: dict[str, object]) -> int:
    raw_timeout = model_resolution.get("timeout_ms")
    if isinstance(raw_timeout, int) and raw_timeout > 0:
        return raw_timeout
    return 60_000


def _deadline_ms(model_resolution: dict[str, object]) -> int:
    raw_deadline = model_resolution.get("deadline_ms")
    if isinstance(raw_deadline, int) and raw_deadline > 0:
        return raw_deadline
    return 120_000


def _max_rounds(model_resolution: dict[str, object]) -> int:
    raw_rounds = model_resolution.get("max_rounds")
    if isinstance(raw_rounds, int) and raw_rounds > 0:
        return min(raw_rounds, 32)
    return 8


def _model_messages(
    context: RuntimeContext,
    session_history: list[MemoryRecord],
    input_message: str,
) -> list[ModelMessage]:
    messages: list[ModelMessage] = []
    system = _system_prompt(context.snapshot.system_prompt, context.snapshot.skill_instructions)
    if system:
        messages.append(ModelMessage(role="system", content=system))
    for record in session_history:
        if record.role in {"user", "assistant"}:
            messages.append(ModelMessage(role=record.role, content=record.content))
    messages.append(ModelMessage(role="user", content=input_message))
    return messages


def _system_prompt(system_prompt: str, skill_instructions: dict[str, str]) -> str:
    sections = [system_prompt] if system_prompt else []
    sections.extend(
        f"## Skill: {skill_id}\n{instructions}"
        for skill_id, instructions in skill_instructions.items()
    )
    return "\n\n".join(sections)


def _remember_tool_call(
    call: ToolCall,
    seen_call_ids: set[str],
    seen_signatures: set[str],
) -> None:
    signature = f"{call.name}:{json.dumps(call.arguments, sort_keys=True, separators=(',', ':'))}"
    if not call.call_id or call.call_id in seen_call_ids or signature in seen_signatures:
        raise AgentLoopDuplicateToolCallError(f"duplicate model tool call: {call.name}")
    seen_call_ids.add(call.call_id)
    seen_signatures.add(signature)


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None
