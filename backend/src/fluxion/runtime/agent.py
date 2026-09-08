from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

from fluxion.kernel.events import (
    ModelCallAttempt,
    ModelCallObserver,
)
from fluxion.plugins.contracts import (
    ModelMessage,
    ModelProviderError,
    ModelProviderRegistryProtocol,
    ModelProviderTimeoutError,
    ModelRequest,
    ModelResponse,
    StreamingModelProvider,
    ToolCall,
    ToolDescriptor,
)
from fluxion.resources import ExecutionSnapshot, ResolvedModelRoute
from fluxion.runtime.attempt_budget import AttemptBudget, fire_after, fire_before
from fluxion.runtime.context import RequestContext, RuntimeContext, TraceEvent
from fluxion.runtime.memory import MemoryManager, MemoryPolicy, MemoryRecord, SessionMemoryStore
from fluxion.runtime.summarizer import SummarizerRegistryProtocol
from fluxion.runtime.tokens import estimate_text_tokens


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


ModelToolHandler = Callable[[RuntimeContext, ToolCall], Awaitable[ModelToolResult]]


def _deadline_exceeded(context: RuntimeContext) -> AgentLoopTimeoutError:
    """累计业务 deadline 耗尽（TASK-007）：统一留痕后抛错。"""
    context.emit(
        "agent_loop.timeout",
        {"deadline_ms": context.snapshot.model_resolution.model_deadline_ms},
    )
    return AgentLoopTimeoutError("agent loop deadline exceeded")


class AgentRuntime:
    def __init__(
        self,
        *,
        snapshot_builder: Any,
        memory_store: SessionMemoryStore,
        memory_policy: MemoryPolicy | None = None,
        model_providers: ModelProviderRegistryProtocol | None = None,
        summarizer_registry: SummarizerRegistryProtocol | None = None,
    ) -> None:
        self._snapshot_builder = snapshot_builder
        self._memory = MemoryManager(
            memory_store,
            policy=memory_policy,
            summarizer_registry=summarizer_registry,
        )
        self._model_providers = model_providers

    @property
    def memory(self) -> MemoryManager:
        return self._memory

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

    async def _prepare_history(self, context: RuntimeContext) -> list[MemoryRecord]:
        """模型调用前共享历史准备（FEAT-09）：读取历史 → maybe_compact →
        如压缩则重读 → 交共同 Prompt 构建函数。

        run_step 与 stream_final_answer 均经此进入 `_model_messages`；幂等
        （重复调用不重复摘要：二次进入时总量已低于阈值）；不请求模型、
        不持久化消息（调用方各负其责）。
        """
        session_history = await self._memory.read_session_context(context)
        # 上下文超预算时摘要压缩：此前 compact_context 是死代码，L1 无界增长
        # 直到 provider context length exceeded（中文场景尤甚——CJK 此前整段
        # 计 1 token 永不触发）。压缩后重读以反映截断后的历史。
        if await self._memory.maybe_compact(context):
            session_history = await self._memory.read_session_context(context)
        return session_history

    async def run_step(
        self,
        context: RuntimeContext,
        input_message: str,
        *,
        tools: Iterable[ToolDescriptor] = (),
        tool_handler: ModelToolHandler | None = None,
        # TASK-001（P1-01）：真实模型调用观测注入点。None 时行为零变化；
        # 内核只依赖 ModelCallObserver 稳定契约，不感知 Hook 总线/Plugin。
        model_call_observer: ModelCallObserver | None = None,
    ) -> RuntimeStepResult:
        session_history = await self._prepare_history(context)
        await self._memory.add_message(context, "user", input_message)
        # FEAT-06：复用 Memory 共享估算修复中文低估；保留「当前输入估算」语义，
        # 口径标记经可扩展事件 payload 明示（TraceEvent.attributes 为开放 dict，
        # 无需契约评审），不冒充 Provider 计费 usage。
        context.emit(
            "execution.step",
            {
                "input_tokens": estimate_text_tokens(input_message),
                "token_source": "estimate",
                "token_scope": "input_message",
            },
        )
        model_response, tool_results = await self._maybe_complete_model(
            context,
            input_message,
            list(tools),
            session_history=session_history,
            tool_handler=tool_handler,
            model_call_observer=model_call_observer,
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

    async def stream_final_answer(
        self,
        context: RuntimeContext,
        input_message: str,
        *,
        model_call_observer: ModelCallObserver | None = None,
    ) -> AsyncIterator[str]:
        """流式输出最终答案 token；provider 不支持流式时返回空迭代（不抛错）。

        仅覆盖单轮最终答案（无 tool call）场景，供 SSE 逐 token 输出；
        有 tool call 需求时调用方回退到 run_step 的非流式完整循环。

        整体受 deadline_ms 约束：非流式路径有 wait_for(deadline_ms) 兜底，而此前
        streaming.stream 无任何 wait_for——卡住的 provider 会永久挂起流式连接。
        这里以整体 deadline 减已耗时作为每轮读取上限，既保留逐 token 增量输出，
        又给流式一个与非流式一致的总截止。
        """
        if self._model_providers is None:
            return
        policy = context.snapshot.model_resolution
        routes = policy.routes
        if not routes:
            return
        # TASK-010：优先 execution-scoped resolver（叠加 store-backed provider），
        # 无则回退 service-level registry。
        resolver = context.model_provider_resolver or self._model_providers
        # TASK-023：显式标记"无路由支持流式"——调用方仅此时可 fallback；
        # 支持但零 token 是正常空结果，不得二次请求模型。
        if not _any_streaming_route(resolver, routes):
            context.emit(
                "model.stream_unsupported",
                {"provider_ids": [route.provider_ref.id for route in routes]},
            )
            return
        # FEAT-09：流式与非流式一致的压缩准备（不额外调 run_step，不重复请求/保存）。
        session_history = await self._prepare_history(context)
        messages = _model_messages(context, session_history, input_message)
        # TASK-007：流式同样走累计业务预算（Hook 等待扣除）；token 读取上限按
        # 剩余预算动态计算，不再用墙钟 deadline。
        budget = AttemptBudget(policy.model_deadline_ms)
        last_error: ModelProviderError | None = None
        # TASK-001：仅真实建流的路由计为 attempt（解析失败/不支持流式直接
        # continue，不产 hook 对；context.emit 保留原有可观测）。
        stream_attempt = 0
        for route in routes:
            provider_id = route.provider_ref.id
            try:
                provider = resolver.resolve(provider_id)
            except ModelProviderError as exc:
                context.emit("model.error", {"provider_id": provider_id, "error": str(exc)})
                last_error = exc
                continue
            if not isinstance(provider, StreamingModelProvider):
                continue
            # TASK-007：预算耗尽时连 before 也不触发。
            if budget.exhausted():
                break
            stream_attempt += 1
            attempt = ModelCallAttempt(
                provider_id=provider_id,
                model=route.model,
                round=1,
                attempt=stream_attempt,
                streaming=True,
            )
            await fire_before(budget, model_call_observer, attempt)
            attempt_started = perf_counter()
            scoped = ModelRequest(
                messages=messages,
                model=route.model,
                timeout_ms=policy.model_timeout_ms,
                tenant_id=context.snapshot.tenant_id,
                user_id=context.snapshot.user_id,
                provider_version=route.provider_ref.version,
                credential_ref=context.snapshot.provider_credentials.get(provider_id),
            )
            emitted = False
            chars = 0
            error: ModelProviderError | None = None
            outcome: str | None = None
            stream = cast(StreamingModelProvider, provider).stream(scoped)
            route_started = perf_counter()
            try:
                while True:
                    global_remaining = budget.remaining_ms() / 1000
                    call_remaining = policy.model_timeout_ms / 1000 - (
                        perf_counter() - route_started
                    )
                    token = await asyncio.wait_for(
                        anext(stream, None), timeout=min(global_remaining, call_remaining)
                    )
                    if token is None:
                        context.emit(
                            "model.completed",
                            {"provider_id": provider_id, "streamed": True},
                        )
                        await fire_after(
                            budget,
                            model_call_observer,
                            attempt,
                            attempt_started,
                            status="ok",
                            output_chars=chars,
                        )
                        return
                    emitted = True
                    chars += len(token)
                    yield token
            except TimeoutError:
                outcome = "timeout"
                error = ModelProviderTimeoutError(
                    f"model provider {provider_id} timed out"
                )
                context.emit(
                    "model.timeout",
                    {"provider_id": provider_id, "timeout_ms": policy.model_timeout_ms},
                )
            except ModelProviderError as exc:
                outcome = "error"
                error = exc
                context.emit("model.error", {"provider_id": provider_id, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001 -- Provider 边界统一转运行时错误
                outcome = "error"
                error = ModelProviderError(f"model provider {provider_id} failed: {exc}")
                context.emit("model.error", {"provider_id": provider_id, "error": str(exc)})
            except asyncio.CancelledError:
                # TASK-007：外部取消成对收尾（error 语义）后继续传播。
                await fire_after(
                    budget,
                    model_call_observer,
                    attempt,
                    attempt_started,
                    status="error",
                    output_chars=0,
                )
                raise
            except GeneratorExit:
                # TASK-007：生成器关闭（aclose）同样收尾后结束，不再产 token。
                await fire_after(
                    budget,
                    model_call_observer,
                    attempt,
                    attempt_started,
                    status="error",
                    output_chars=0,
                )
                return
            finally:
                await stream.aclose()
            if outcome is not None:
                await fire_after(
                    budget,
                    model_call_observer,
                    attempt,
                    attempt_started,
                    status=outcome,
                    output_chars=0,
                )
            if emitted and error is not None:
                raise error
            last_error = error
        if budget.exhausted():
            context.emit("agent_loop.timeout", {"deadline_ms": policy.model_deadline_ms})
            raise AgentLoopTimeoutError("streaming deadline exceeded")
        if last_error is not None:
            raise last_error

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
        tools: list[ToolDescriptor],
        *,
        session_history: list[MemoryRecord],
        tool_handler: ModelToolHandler | None,
        model_call_observer: ModelCallObserver | None = None,
    ) -> tuple[ModelResponse | None, tuple[dict[str, object], ...]]:
        if self._model_providers is None:
            return None, ()
        policy = context.snapshot.model_resolution
        routes = policy.routes
        if not routes:
            return None, ()
        messages = _model_messages(context, session_history, input_message)
        # TASK-007：累计 deadline 改由 _run_model_loop 内显式预算强制（含 tool 耗时、
        # 不含 Hook 等待）；此处不再外层 wait_for——外层统一截断会取消合法的 Hook
        # 上报等待，把慢 Hook 的成功翻成 deadline 失败（S-MB-01）。
        return await self._run_model_loop(
            context,
            routes=routes,
            messages=messages,
            tools=tools,
            timeout_ms=policy.model_timeout_ms,
            tool_handler=tool_handler,
            model_call_observer=model_call_observer,
        )

    async def _run_model_loop(
        self,
        context: RuntimeContext,
        *,
        routes: list[ResolvedModelRoute],
        messages: list[ModelMessage],
        tools: list[ToolDescriptor],
        timeout_ms: int,
        tool_handler: ModelToolHandler | None,
        model_call_observer: ModelCallObserver | None = None,
    ) -> tuple[ModelResponse, tuple[dict[str, object], ...]]:
        max_rounds = context.snapshot.model_resolution.max_rounds
        # TASK-007：整轮共享的累计业务预算（Hook 等待扣除）；tool 耗时计入。
        budget = AttemptBudget(context.snapshot.model_resolution.model_deadline_ms)
        seen_call_ids: set[str] = set()
        seen_signatures: set[str] = set()
        tool_results: list[dict[str, object]] = []
        for round_index in range(1, max_rounds + 1):
            request = ModelRequest(
                messages=list(messages),
                tools=tools,
                timeout_ms=timeout_ms,
                model=routes[0].model,
            )
            if budget.exhausted():
                raise _deadline_exceeded(context)
            response = await self._complete_with_failover(
                context,
                routes,
                request,
                timeout_ms,
                round_index=round_index,
                model_call_observer=model_call_observer,
                budget=budget,
            )
            if not response.tool_calls:
                return response, tuple(tool_results)
            if tool_handler is None:
                return response, tuple(tool_results)
            # TASK-007：tool 段同样受累计预算约束（替代此前外层 wait_for 对挂起
            # tool 的兜底）；模型侧 attempt 已闭环，此处超时无未结 pair。
            try:
                await asyncio.wait_for(
                    self._run_round_tools(
                        context,
                        messages,
                        response,
                        tool_handler,
                        tool_results,
                        seen_call_ids,
                        seen_signatures,
                    ),
                    timeout=budget.remaining_ms() / 1000,
                )
            except TimeoutError:
                raise _deadline_exceeded(context)
            context.emit(
                "agent_loop.round_completed",
                {"round": round_index, "tool_call_count": len(response.tool_calls)},
            )
        context.emit("agent_loop.limit_exceeded", {"max_rounds": max_rounds})
        raise AgentLoopLimitError(f"agent loop exceeded {max_rounds} rounds")

    async def _run_round_tools(
        self,
        context: RuntimeContext,
        messages: list[ModelMessage],
        response: ModelResponse,
        tool_handler: ModelToolHandler,
        tool_results: list[dict[str, object]],
        seen_call_ids: set[str],
        seen_signatures: set[str],
    ) -> None:
        """单轮 tool 调用处理（TASK-007：由调用方按累计预算约束）。"""
        messages.append(
            ModelMessage(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            )
        )
        for call in response.tool_calls:
            call = _ensure_call_id(call)
            if _remember_tool_call(call, seen_call_ids, seen_signatures):
                # 重复调用（同 id 或同名同参）：不硬失败也不重复执行
                # （避免副作用双发）；把 "已调用过" 作为 tool result 喂回，
                # 让模型改道，循环仍由 max_rounds 兜底。此前直接 raise
                # 会因模型合法的重复查询（轮询/重读）终止整个 execution。
                result = ModelToolResult(
                    call_id=call.call_id,
                    tool_id=call.name,
                    content=(
                        f"tool {call.name} already called with identical "
                        "arguments; change approach"
                    ),
                    payload={"tool_id": call.name, "duplicate": True},
                )
            else:
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

    async def _complete_with_failover(
        self,
        context: RuntimeContext,
        routes: list[ResolvedModelRoute],
        request: ModelRequest,
        timeout_ms: int,
        *,
        round_index: int = 1,
        model_call_observer: ModelCallObserver | None = None,
        budget: AttemptBudget,
    ) -> ModelResponse:
        last_error: ModelProviderError | None = None
        # TASK-001：每个真实 _complete_once 即一次 attempt（1-based），前后各一次
        # observer 回调；observer 缺省时控制流与此前逐字一致。
        # TASK-007：累计预算在 attempt 级强制——Hook 等待已扣除；超时按来源分流
        # （业务 deadline 耗尽 abort，单 provider 超时继续 failover）；外部取消也
        # 成对收尾后继续传播。
        for attempt_no, route in enumerate(routes, start=1):
            provider_id = route.provider_ref.id
            attempt = ModelCallAttempt(
                provider_id=provider_id,
                model=route.model,
                round=round_index,
                attempt=attempt_no,
            )
            # 预算耗尽时连 before 也不触发（不制造未开始调用的 after）。
            if budget.exhausted():
                raise _deadline_exceeded(context)
            await fire_before(budget, model_call_observer, attempt)
            # before 自身耗时已扣除；本次调用上限取业务超时与剩余预算之小。
            attempt_timeout_ms = min(timeout_ms, max(budget.remaining_ms(), 0.0))
            attempt_started = perf_counter()
            try:
                response = await asyncio.wait_for(
                    self._complete_once(
                        context,
                        provider_id,
                        replace(request, model=route.model),
                        timeout_ms,
                    ),
                    timeout=attempt_timeout_ms / 1000,
                )
            except TimeoutError:
                await fire_after(
                    budget,
                    model_call_observer,
                    attempt,
                    attempt_started,
                    status="timeout",
                    output_chars=0,
                )
                if budget.exhausted():
                    raise _deadline_exceeded(context)
                last_error = ModelProviderTimeoutError(
                    f"model provider {provider_id} timed out"
                )
                context.emit("model.timeout", {"provider_id": provider_id, "timeout_ms": timeout_ms})
                continue
            except ModelProviderTimeoutError as exc:
                await fire_after(
                    budget,
                    model_call_observer,
                    attempt,
                    attempt_started,
                    status="timeout",
                    output_chars=0,
                )
                context.emit("model.timeout", {"provider_id": provider_id, "timeout_ms": timeout_ms})
                last_error = exc
                continue
            except ModelProviderError as exc:
                await fire_after(
                    budget,
                    model_call_observer,
                    attempt,
                    attempt_started,
                    status="error",
                    output_chars=0,
                )
                context.emit("model.error", {"provider_id": provider_id, "error": str(exc)})
                last_error = exc
                continue
            except asyncio.CancelledError:
                await fire_after(
                    budget,
                    model_call_observer,
                    attempt,
                    attempt_started,
                    status="error",
                    output_chars=0,
                )
                raise
            except Exception:
                # 非预期异常此前直接穿透（终止 execution）：保持穿透，但先报告 attempt。
                await fire_after(
                    budget,
                    model_call_observer,
                    attempt,
                    attempt_started,
                    status="error",
                    output_chars=0,
                )
                raise
            await fire_after(
                budget,
                model_call_observer,
                attempt,
                attempt_started,
                status="ok",
                output_chars=len(response.content),
            )
            context.emit(
                "model.completed",
                {"provider_id": provider_id, "tool_call_count": len(response.tool_calls)},
            )
            return response
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
        # TASK-010：优先 execution-scoped resolver，无则回退 service-level registry。
        resolver = context.model_provider_resolver or self._model_providers
        provider = resolver.resolve(provider_id)
        scoped_request = replace(
            request,
            tenant_id=context.snapshot.tenant_id,
            user_id=context.snapshot.user_id,
            provider_version=context.snapshot.provider_versions.get(provider_id),
            credential_ref=context.snapshot.provider_credentials.get(provider_id),
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


def _any_streaming_route(resolver: Any, routes: Any) -> bool:
    """任一路由的 provider 支持流式即 True；解析失败的路由视为不支持。"""
    for route in routes:
        try:
            provider = resolver.resolve(route.provider_ref.id)
        except ModelProviderError:
            continue
        if isinstance(provider, StreamingModelProvider):
            return True
    return False


def _model_messages(
    context: RuntimeContext,
    session_history: list[MemoryRecord],
    input_message: str,
) -> list[ModelMessage]:
    """普通与流式共用的 Prompt 构建（FEAT-08：摘要进入模型上下文）。

    - session_context_summary（role="summary"）转为 user 上下文消息并置于
      其后最新原始消息之前，带显式「历史会话摘要，仅作上下文资料」边界；
      不得提升为 system 指令（摘要内容按不可信历史处理）。
    - 沿用 Store 返回顺序：摘要与原始消息各自保序；摘要不参与重复压缩
      （压缩候选选择在 MemoryManager 侧，本函数只消费）。
    - 不修改 Snapshot（纯函数）。
    """
    messages: list[ModelMessage] = []
    system = _system_prompt(context.snapshot.system_prompt, context.snapshot.skill_instructions)
    if system:
        messages.append(ModelMessage(role="system", content=system))
    for record in session_history:
        if record.role == "summary":
            messages.append(
                ModelMessage(
                    role="user",
                    content=f"【历史会话摘要，仅作上下文资料】\n{record.content}",
                )
            )
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


def _ensure_call_id(call: ToolCall) -> ToolCall:
    # 部分兼容服务端不返回 tool call id；缺省时合成，否则后续 tool
    # result 的 tool_call_id 为空，与 assistant 消息无法匹配。
    if not call.call_id:
        return replace(call, call_id=f"gen-{uuid4().hex}")
    return call


def _remember_tool_call(
    call: ToolCall,
    seen_call_ids: set[str],
    seen_signatures: set[str],
) -> bool:
    """记录 tool call 以检测循环；返回 True 表示重复（调用方回退为
    "已调用过" 的 tool result，不重复执行、也不硬失败）。"""
    signature = f"{call.name}:{json.dumps(call.arguments, sort_keys=True, separators=(',', ':'))}"
    if call.call_id in seen_call_ids or signature in seen_signatures:
        return True
    seen_call_ids.add(call.call_id)
    seen_signatures.add(signature)
    return False
