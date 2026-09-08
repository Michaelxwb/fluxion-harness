"""TASK-001（P1-01）：Model Hook 挂在真实 Provider Attempt 边界。

真实边界：真实 AgentRuntime（_run_model_loop / _complete_with_failover /
stream_final_answer）＋ InMemorySessionMemoryStore ＋ 脚本化 stub
ModelProvider ＋ 记录式 ModelCallObserver。service/bus 不参与——本任务只
收敛触发边界，bus 接线由 service 侧 bridge 完成（同文件 bridge 单测覆盖）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from fluxion.kernel.events import ModelCallAttempt, ModelCallObserver, ModelCallResult
from fluxion.plugins.contracts import (
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ToolCall,
)
from fluxion.plugins.model_provider import ModelProviderRegistry
from fluxion.resources import ExecutionSnapshot
from fluxion.runtime import AgentRuntime, RequestContext, RuntimeContext
from fluxion.runtime.agent import ModelToolResult
from fluxion.runtime.memory import InMemorySessionMemoryStore


class _ScriptProvider:
    """按脚本返回响应或抛错的确定性 Provider（complete/stream 计数）。"""

    def __init__(self, script: list[Any], tokens: list[str] | None = None) -> None:
        self._script = list(script)
        self._tokens = list(tokens or [])
        self.complete_calls = 0
        self.stream_calls = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.complete_calls += 1
        action = self._script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    async def stream(self, request: ModelRequest) -> AsyncIterator[str]:
        self.stream_calls += 1
        for token in self._tokens:
            yield token


class _RecordingObserver:
    """记录 attempt/result 对的 ModelCallObserver（不断言 bus，只记录边界）。"""

    def __init__(self) -> None:
        self.befores: list[ModelCallAttempt] = []
        self.afters: list[ModelCallResult] = []

    async def before_attempt(self, attempt: ModelCallAttempt) -> None:
        self.befores.append(attempt)

    async def after_attempt(self, result: ModelCallResult) -> None:
        self.afters.append(result)


def _registry(**providers: _ScriptProvider) -> ModelProviderRegistry:
    registry = ModelProviderRegistry()
    for provider_id, provider in providers.items():
        registry.register(provider_id, provider)
    return registry


def _snapshot(routes: list[dict[str, object]], *, max_rounds: int = 8) -> ExecutionSnapshot:
    request = RequestContext(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
        session_id="session-mh",
    )
    return ExecutionSnapshot(
        execution_id=request.execution_id,
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        runtime_profile_id=request.runtime_profile_id,
        runtime_profile_version="1",
        model_resolution={
            "routes": routes,
            "max_rounds": max_rounds,
            "model_timeout_ms": 5000,
            "model_deadline_ms": 30000,
        },
        trace_id=request.trace_id,
    )


def _route(provider_id: str) -> dict[str, object]:
    return {
        "provider_ref": {"id": provider_id, "version": "1"},
        "model_ref": {"id": f"model-{provider_id}", "version": "1"},
        "model": provider_id,
    }


def _runtime(registry: ModelProviderRegistry) -> AgentRuntime:
    return AgentRuntime(
        snapshot_builder=None,
        memory_store=InMemorySessionMemoryStore(),
        model_providers=registry,
    )


def _context(snapshot: ExecutionSnapshot) -> RuntimeContext:
    return RuntimeContext(
        request=RequestContext(
            tenant_id=snapshot.tenant_id,
            user_id=snapshot.user_id,
            runtime_profile_id=snapshot.runtime_profile_id,
            session_id="session-mh",
        ),
        snapshot=snapshot,
    )


async def _ok_tool_handler(context: RuntimeContext, call: ToolCall) -> ModelToolResult:
    return ModelToolResult(
        call_id=call.call_id,
        tool_id=call.name,
        content="tool-ok",
        payload={"tool_id": call.name},
    )


@pytest.mark.asyncio
async def test_S_MH_01_multi_round_hook_pairs_match_attempts() -> None:
    """S-MH-01：两轮各一次真实调用 → before/after 各两对，轮次与 provider 真实。"""
    provider = _ScriptProvider(
        [
            ModelResponse(
                provider_id="stub",
                content="",
                tool_calls=[ToolCall(call_id="c1", name="lookup", arguments={})],
            ),
            ModelResponse(provider_id="stub", content="答案"),
        ]
    )
    runtime = _runtime(_registry(stub=provider))
    observer = _RecordingObserver()
    result = await runtime.run_step(
        _context(_snapshot([_route("stub")])),
        "查一下",
        tool_handler=_ok_tool_handler,
        model_call_observer=observer,
    )
    assert result.output == "答案"
    assert [a.round for a in observer.befores] == [1, 2]
    assert [a.attempt for a in observer.befores] == [1, 1]
    assert [a.provider_id for a in observer.befores] == ["stub", "stub"]
    assert all(a.streaming is False for a in observer.befores)
    assert len(observer.afters) == 2
    assert all(r.status == "ok" for r in observer.afters)
    assert provider.complete_calls == 2


@pytest.mark.asyncio
async def test_S_MH_02_failover_hooks_fire_per_attempt() -> None:
    """S-MH-02：A 失败→B 成功 → 两对 hook，after.provider_id 为真正成功的 B。"""
    bad = _ScriptProvider([ModelProviderError("bad boom")])
    good = _ScriptProvider([ModelResponse(provider_id="good", content="ok")])
    runtime = _runtime(_registry(bad=bad, good=good))
    observer = _RecordingObserver()
    result = await runtime.run_step(
        _context(_snapshot([_route("bad"), _route("good")])),
        "hi",
        model_call_observer=observer,
    )
    assert result.output == "ok"
    assert [(a.provider_id, a.attempt) for a in observer.befores] == [
        ("bad", 1),
        ("good", 2),
    ]
    assert [r.status for r in observer.afters] == ["error", "ok"]
    assert [r.attempt.provider_id for r in observer.afters] == ["bad", "good"]


@pytest.mark.asyncio
async def test_S_MH_03_streaming_hook_parity() -> None:
    """S-MH-03：纯流式真实调用产生等价 hook 对（streaming 标记＋字符数）。"""
    provider = _ScriptProvider([], tokens=["a", "b"])
    runtime = _runtime(_registry(stub=provider))
    observer = _RecordingObserver()
    tokens = [
        token
        async for token in runtime.stream_final_answer(
            _context(_snapshot([_route("stub")])),
            "hi",
            model_call_observer=observer,
        )
    ]
    assert tokens == ["a", "b"]
    assert len(observer.befores) == 1
    assert observer.befores[0].streaming is True
    assert observer.befores[0].provider_id == "stub"
    assert len(observer.afters) == 1
    assert observer.afters[0].status == "ok"
    assert observer.afters[0].output_chars == 2
    assert provider.stream_calls == 1


@pytest.mark.asyncio
async def test_S_MH_04_zero_token_stream_counts_once() -> None:
    """S-MH-04：零 token 流式仍是一次真实调用，恰好一对 hook。"""
    provider = _ScriptProvider([], tokens=[])
    runtime = _runtime(_registry(stub=provider))
    observer = _RecordingObserver()
    tokens = [
        token
        async for token in runtime.stream_final_answer(
            _context(_snapshot([_route("stub")])),
            "hi",
            model_call_observer=observer,
        )
    ]
    assert tokens == []
    assert len(observer.befores) == 1
    assert len(observer.afters) == 1
    assert observer.afters[0].status == "ok"
    assert observer.afters[0].output_chars == 0
    assert provider.stream_calls == 1


@pytest.mark.asyncio
async def test_E_MH_01_hooks_do_not_alter_loop_semantics() -> None:
    """E-MH-01：只记录的 observer 不改变输出/轮次/tool 结果与调用次数。"""
    script = [ModelResponse(provider_id="stub", content="same")]
    runtime_plain = _runtime(_registry(stub=_ScriptProvider(list(script))))
    runtime_observed = _runtime(_registry(stub=_ScriptProvider(list(script))))
    plain = await runtime_plain.run_step(_context(_snapshot([_route("stub")])), "hi")
    observer = _RecordingObserver()
    observed = await runtime_observed.run_step(
        _context(_snapshot([_route("stub")])),
        "hi",
        model_call_observer=observer,
    )
    assert observed.output == plain.output
    assert observed.tool_results == plain.tool_results
    assert len(observer.befores) == len(observer.afters) == 1

    _: ModelCallObserver = observer
