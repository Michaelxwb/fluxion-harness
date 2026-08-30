"""ADR-MEM-001 TASK-002 验收测试：Summarizer SPI 替换假 `_summarize`。

S-03 / E-02（integration，RULE-backend-quality-001 verifier: doublewrite-summarizer-spi）。
真实边界：真实 `compact_context`（AgentRuntime → MemoryManager）+ 真实
`SummarizerRegistry` resolve + 真实 fallback 路径；model 超时/异常用真实
Offline/Timeout/Fixed ModelProvider 实现触发（非 unittest.mock——SPI 分派与
降级均为真实代码路径，测试类只提供真实 Provider/Summarizer 实现）。
"""

from __future__ import annotations

import asyncio
import time

import pytest
from tests.runtime_helpers import seed_runtime_profile

from fluxion.plugins.contracts import (
    ModelProviderError,
    ModelRequest,
    ModelResponse,
)
from fluxion.registry import RegistryStore
from fluxion.runtime import AgentRuntime, RequestContext
from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.memory import InMemorySessionMemoryStore, MemoryPolicy
from fluxion.runtime.resolver import ResourceResolver
from fluxion.services.context_resolver import ContextResolver, ContextResolverSnapshotBuilder
from fluxion.runtime.summarizer import (
    DeterministicTruncationSummarizer,
    ModelSummarizer,
    SummarizerRegistry,
    SummaryResult,
    compute_source_range_hash,
)


# --------------------------------------------------------------------------- #
# 真实测试替身：均为 SPI/Provider 的真实实现（非 mock），驱动真实分派/降级路径
# --------------------------------------------------------------------------- #


class RecordingSummarizer:
    """真实 Summarizer 实现：大写拼接并记录调用参数（证明 SPI 分派与 token_budget）。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def summarize(self, records, *, token_budget: int) -> SummaryResult:
        self.calls.append({"count": len(records), "token_budget": token_budget})
        content = " || ".join(record.content.upper() for record in records)
        return SummaryResult(content=content, source_range_hash="hash-from-spi")


class OfflineModelProvider:
    """真实 ModelProvider：complete 永远失败（模拟 model 不可用/连接拒绝）。"""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        raise ModelProviderError("connection refused")


class TimeoutModelProvider:
    """真实 ModelProvider：complete 超时（模拟 model 慢调用，触发 wait_for 超时）。"""

    def __init__(self, delay_seconds: float = 5.0) -> None:
        self._delay = delay_seconds

    async def complete(self, request: ModelRequest) -> ModelResponse:
        await asyncio.sleep(self._delay)
        return ModelResponse(provider_id="timeout-provider", content="never")


class FixedModelProvider:
    """真实 ModelProvider：返回固定摘要内容（模拟 model 正常输出）。"""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(provider_id="fixed-provider", content="model-generated summary")


async def _make_runtime(
    sqlite_store: RegistryStore,
    memory_store: InMemorySessionMemoryStore,
    summarizer_registry: SummarizerRegistry,
) -> tuple[AgentRuntime, RuntimeContext, list]:
    """建 runtime + 5 轮消息；返回 compact 前的 older 记录（retain=2 → 前 3 条）。"""
    await seed_runtime_profile(sqlite_store)
    runtime = AgentRuntime(
        snapshot_builder=ContextResolverSnapshotBuilder(ContextResolver(sqlite_store)),
        memory_store=memory_store,
        memory_policy=MemoryPolicy(max_context_tokens=12, retain_latest_turns=2),
        summarizer_registry=summarizer_registry,
    )
    context = await runtime.start_execution(
        RequestContext(
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            session_id="session-a",
        )
    )
    for index in range(5):
        await runtime.memory.add_message(context, "user", f"turn-{index}")
    messages = runtime.memory.l0_messages(context.snapshot.execution_id)
    older = messages[:-2]
    return runtime, context, older


def _fallback_events(context: RuntimeContext) -> list:
    return [event for event in context.trace if event.name == "memory.summarizer_fallback"]


# --------------------------------------------------------------------------- #
# S-03
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_s03_compact_uses_summarizer_spi_not_string_concat(
    sqlite_store: RegistryStore,
) -> None:
    """compact_context 经 registry resolve 调 Summarizer SPI，非 `_summarize` 拼接。"""
    summarizer = RecordingSummarizer()
    registry = SummarizerRegistry()
    registry.register("model", summarizer)
    registry.register("deterministic", DeterministicTruncationSummarizer())
    memory_store = InMemorySessionMemoryStore()
    runtime, context, _ = await _make_runtime(sqlite_store, memory_store, registry)

    compacted = await runtime.memory.compact_context(context)

    # SPI 被调用且收到 token_budget（= policy.max_context_tokens，§4.6 行 277 硬要求）
    assert summarizer.calls == [{"count": 3, "token_budget": 12}]
    # 摘要来自 SPI 输出，而非旧 `_summarize` 的 "summary: a | b" 拼接格式
    assert compacted.summary == "TURN-0 || TURN-1 || TURN-2"
    assert not compacted.summary.startswith("summary: ")
    # summary 带 source_range_hash（可追溯）
    assert compacted.source_range_hash == "hash-from-spi"
    summaries = await memory_store.read_summaries("tenant-a", "session-a")
    assert summaries[-1].content == compacted.summary


@pytest.mark.asyncio
async def test_s03_model_summarizer_via_registry_uses_model_output(
    sqlite_store: RegistryStore,
) -> None:
    """ModelSummarizer 经 registry 分派：摘要 = model 输出 + 真实 source_range_hash。"""
    registry = SummarizerRegistry()
    registry.register("model", ModelSummarizer(FixedModelProvider()))
    registry.register("deterministic", DeterministicTruncationSummarizer())
    memory_store = InMemorySessionMemoryStore()
    runtime, context, older = await _make_runtime(sqlite_store, memory_store, registry)

    compacted = await runtime.memory.compact_context(context)

    assert compacted.summary == "model-generated summary"
    assert compacted.source_range_hash == compute_source_range_hash(older)
    assert len(compacted.source_range_hash) == 64


@pytest.mark.asyncio
async def test_s03_model_unavailable_falls_back_to_deterministic_truncation(
    sqlite_store: RegistryStore,
) -> None:
    """model 不可用（provider 异常）→ 降级 DeterministicTruncationSummarizer。"""
    registry = SummarizerRegistry()
    registry.register("model", ModelSummarizer(OfflineModelProvider()))
    registry.register("deterministic", DeterministicTruncationSummarizer())
    memory_store = InMemorySessionMemoryStore()
    runtime, context, older = await _make_runtime(sqlite_store, memory_store, registry)

    compacted = await runtime.memory.compact_context(context)

    expected = await DeterministicTruncationSummarizer().summarize(older, token_budget=12)
    assert compacted.summary == expected.content
    assert compacted.source_range_hash == expected.source_range_hash
    assert _fallback_events(context), "降级必须可观测（trace 事件），不得静默"


# --------------------------------------------------------------------------- #
# E-02
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_e02_model_timeout_falls_back_with_log_and_trace_without_blocking(
    sqlite_store: RegistryStore, caplog: pytest.LogCaptureFixture
) -> None:
    """model 超时 → 降级 fallback（带日志+trace），不阻断主对话。"""
    registry = SummarizerRegistry()
    registry.register(
        "model", ModelSummarizer(TimeoutModelProvider(delay_seconds=5.0), timeout_ms=50)
    )
    registry.register("deterministic", DeterministicTruncationSummarizer())
    memory_store = InMemorySessionMemoryStore()
    runtime, context, older = await _make_runtime(sqlite_store, memory_store, registry)

    started = time.monotonic()
    with caplog.at_level("WARNING", logger="fluxion.runtime.memory"):
        compacted = await runtime.memory.compact_context(context)
    elapsed = time.monotonic() - started

    # 不阻断：远小于 provider 的 5s 延迟（wait_for 50ms 兜底）
    assert elapsed < 1.0
    # 降级结果 = 确定性截断
    expected = await DeterministicTruncationSummarizer().summarize(older, token_budget=12)
    assert compacted.summary == expected.content

    # trace：降级事件关联 trace_id + error_type
    events = _fallback_events(context)
    assert events, "降级必须有 trace 事件"
    assert events[0].trace_id == context.snapshot.trace_id
    assert events[0].attributes["error_type"] == "TimeoutError"
    assert events[0].attributes["fallback"] == "deterministic"

    # 日志：结构化 warning 携带 trace_id（不静默吞）
    warnings = [
        record
        for record in caplog.records
        if record.name == "fluxion.runtime.memory" and record.levelname == "WARNING"
    ]
    assert warnings and context.snapshot.trace_id in warnings[-1].getMessage()


@pytest.mark.asyncio
async def test_e02_model_error_falls_back_not_silent(
    sqlite_store: RegistryStore, caplog: pytest.LogCaptureFixture
) -> None:
    """model 异常（provider error）→ 降级 fallback，日志+trace 记录 error_type。"""
    registry = SummarizerRegistry()
    registry.register("model", ModelSummarizer(OfflineModelProvider()))
    registry.register("deterministic", DeterministicTruncationSummarizer())
    memory_store = InMemorySessionMemoryStore()
    runtime, context, older = await _make_runtime(sqlite_store, memory_store, registry)

    with caplog.at_level("WARNING", logger="fluxion.runtime.memory"):
        compacted = await runtime.memory.compact_context(context)

    expected = await DeterministicTruncationSummarizer().summarize(older, token_budget=12)
    assert compacted.summary == expected.content

    events = _fallback_events(context)
    assert events[0].attributes["error_type"] == "ModelProviderError"
    assert events[0].trace_id == context.snapshot.trace_id
    warnings = [
        record
        for record in caplog.records
        if record.name == "fluxion.runtime.memory" and record.levelname == "WARNING"
    ]
    assert warnings and "ModelProviderError" in warnings[-1].getMessage()
