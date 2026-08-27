"""ADR-MEM-001 Summarizer SPI：session compaction 摘要的内部 SPI（design §3.3 形态 C）。

替换旧 `_summarize` 字符串拼接（假摘要）。组成：
- `Summarizer` Protocol + `SummaryResult(content, source_range_hash)`
- `ModelSummarizer`：调 ModelProvider 生成摘要（外部调用，带 timeout）
- `DeterministicTruncationSummarizer`：确定性截断 fallback（无外部依赖，保底可用）
- `SummarizerRegistry` / `SummarizerRegistryProtocol`：镜像 ModelProviderRegistry 模式

失败策略：model 超时/异常由 MemoryManager 降级 deterministic fallback，
不静默吞（trace 事件 + 结构化日志，E-02）。
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from fluxion.plugins.contracts import ModelMessage, ModelProvider, ModelRequest

if TYPE_CHECKING:
    from fluxion.runtime.memory import MemoryRecord

SUMMARIZER_MODEL = "model"
SUMMARIZER_DETERMINISTIC = "deterministic"


@dataclass(frozen=True, slots=True)
class SummaryResult:
    """compaction 摘要结果：内容 + 源消息 range hash（可追溯，§3.3 接口设计）。"""

    content: str
    source_range_hash: str


class SummarizerError(RuntimeError):
    code = "summarizer_error"


class SummarizerNotFoundError(SummarizerError):
    """typed summarizer registry resolve 未命中（语义化错误码，镜像 ProviderNotFoundError）。"""

    code = "summarizer_not_found"


class Summarizer(Protocol):
    async def summarize(self, records: list[MemoryRecord], *, token_budget: int) -> SummaryResult: ...


class SummarizerRegistryProtocol(Protocol):
    def register(self, summarizer_id: str, summarizer: Summarizer) -> None: ...

    def resolve(self, summarizer_id: str) -> Summarizer: ...


class SummarizerRegistry:
    """镜像 ModelProviderRegistry：typed register/resolve，resolve 未命中抛语义化错误。"""

    def __init__(self) -> None:
        self._summarizers: dict[str, Summarizer] = {}

    def register(self, summarizer_id: str, summarizer: Summarizer) -> None:
        if not summarizer_id.strip():
            raise ValueError("summarizer_id is required")
        self._summarizers[summarizer_id] = summarizer

    def resolve(self, summarizer_id: str) -> Summarizer:
        summarizer = self._summarizers.get(summarizer_id)
        if summarizer is None:
            raise SummarizerNotFoundError(f"summarizer {summarizer_id} not found")
        return summarizer

    def summarizer_ids(self) -> list[str]:
        return list(self._summarizers)


def compute_source_range_hash(records: list[MemoryRecord]) -> str:
    """源消息 range 的确定性 hash（sha256 hex，64 字符，可追溯写入 personal_memory）。"""
    digest = hashlib.sha256()
    for record in records:
        digest.update(
            f"{record.execution_id}\x1f{record.role}\x1f{record.content}\x1f{record.tokens}".encode()
        )
    return digest.hexdigest()


class DeterministicTruncationSummarizer:
    """确定性截断 fallback：同输入同输出、无外部依赖，model 不可用时保底（E-02）。

    截断规则：按顺序累加整条记录，估算 token 超预算即停（至少保留首条）；
    单条仍超预算时按字符截断到估算达标，保证输出有界。
    """

    async def summarize(self, records: list[MemoryRecord], *, token_budget: int) -> SummaryResult:
        selected: list[str] = []
        used = 0
        for record in records:
            if selected and used + record.tokens > token_budget:
                break
            selected.append(record.content)
            used += record.tokens
        content = _cut_to_token_budget(" | ".join(selected), token_budget)
        return SummaryResult(content=content, source_range_hash=compute_source_range_hash(records))


class ModelSummarizer:
    """调 ModelProvider 生成摘要。外部调用必须有限时（timeout_ms + wait_for 兜底）。"""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        timeout_ms: int = 30_000,
        model: str | None = None,
    ) -> None:
        self._provider = provider
        self._timeout_ms = timeout_ms
        self._model = model

    async def summarize(self, records: list[MemoryRecord], *, token_budget: int) -> SummaryResult:
        instruction = (
            f"Summarize the following conversation history in at most {token_budget} tokens. "
            "Reply with the summary text only."
        )
        messages = [ModelMessage(role="system", content=instruction)]
        messages.extend(
            ModelMessage(
                role=record.role if record.role in ("user", "assistant") else "user",
                content=record.content,
            )
            for record in records
        )
        request = ModelRequest(
            messages=messages,
            timeout_ms=self._timeout_ms,
            model=self._model,
            tenant_id=records[0].tenant_id if records else None,
            user_id=records[0].user_id if records else None,
        )
        response = await asyncio.wait_for(
            self._provider.complete(request), timeout=self._timeout_ms / 1000
        )
        return SummaryResult(
            content=response.content,
            source_range_hash=compute_source_range_hash(records),
        )


def _cut_to_token_budget(content: str, token_budget: int) -> str:
    """字符级确定性截断：估算 token ≤ budget 的最长前缀（CJK 1字≈1token 的粗估足够 fallback）。"""
    # function-level import 打断 memory ↔ summarizer 循环（memory 顶层依赖本模块）
    from fluxion.runtime.memory import _estimate_tokens

    if _estimate_tokens(content) <= token_budget:
        return content
    best = ""
    for end in range(1, len(content) + 1):
        candidate = content[:end]
        if _estimate_tokens(candidate) > token_budget:
            break
        best = candidate
    return best


def default_summarizer_registry() -> SummarizerRegistry:
    """默认 registry：无 model 配置时只挂 deterministic（纯函数实现，无外部依赖）。"""
    registry = SummarizerRegistry()
    registry.register(SUMMARIZER_DETERMINISTIC, DeterministicTruncationSummarizer())
    return registry
