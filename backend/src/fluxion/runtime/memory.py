from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fluxion.observability.logging import emit_memory_event_log
from fluxion.runtime.context import RuntimeContext
from fluxion.runtime.summarizer import (
    SUMMARIZER_DETERMINISTIC,
    SUMMARIZER_MODEL,
    SummarizerRegistryProtocol,
    SummaryResult,
    default_summarizer_registry,
)


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    tenant_id: str
    user_id: str
    session_id: str
    execution_id: str
    role: str
    content: str
    tokens: int


@dataclass(frozen=True, slots=True)
class MemoryPolicy:
    max_context_tokens: int = 4000
    flush_threshold_ratio: float = 0.8
    retain_latest_turns: int = 4

    def __post_init__(self) -> None:
        if self.max_context_tokens <= 0:
            raise ValueError("max_context_tokens must be positive")
        if not 0 < self.flush_threshold_ratio <= 1:
            raise ValueError("flush_threshold_ratio must be in (0, 1]")
        if self.retain_latest_turns <= 0:
            raise ValueError("retain_latest_turns must be positive")


@dataclass(frozen=True, slots=True)
class CompactionResult:
    raw_messages: list[MemoryRecord]
    summary: str
    # ADR-MEM-001：摘要源消息 range hash（Summarizer SPI 产出，可追溯）
    source_range_hash: str = ""


class SessionMemoryStore(Protocol):
    async def append_l1(self, records: list[MemoryRecord]) -> None: ...

    async def append_l2(self, records: list[MemoryRecord]) -> None: ...

    async def append_summary(self, record: MemoryRecord) -> None: ...

    async def read_l1(self, tenant_id: str, session_id: str) -> list[MemoryRecord]: ...

    async def read_l2(self, tenant_id: str, user_id: str) -> list[MemoryRecord]: ...

    async def read_summaries(self, tenant_id: str, session_id: str) -> list[MemoryRecord]: ...

    async def remove_l1(self, records: list[MemoryRecord]) -> None: ...


class InMemorySessionMemoryStore:
    def __init__(self) -> None:
        self._l1: dict[tuple[str, str], list[MemoryRecord]] = {}
        self._l2: dict[tuple[str, str], list[MemoryRecord]] = {}
        self._summaries: dict[tuple[str, str], list[MemoryRecord]] = {}

    async def append_l1(self, records: list[MemoryRecord]) -> None:
        for record in records:
            self._l1.setdefault((record.tenant_id, record.session_id), []).append(record)

    async def append_l2(self, records: list[MemoryRecord]) -> None:
        for record in records:
            self._l2.setdefault((record.tenant_id, record.user_id), []).append(record)

    async def append_summary(self, record: MemoryRecord) -> None:
        key = (record.tenant_id, record.session_id)
        self._summaries.setdefault(key, []).append(record)
        # ADR-MEM-001：SessionContextSummary 只写 session-scoped（_summaries/_l1），
        # 不交叉写 _l2——summary 泄漏进 user-level L2 的写侧根因已删。
        await self.append_l1([record])

    async def read_l1(self, tenant_id: str, session_id: str) -> list[MemoryRecord]:
        return list(self._l1.get((tenant_id, session_id), []))

    async def read_l2(self, tenant_id: str, user_id: str) -> list[MemoryRecord]:
        return list(self._l2.get((tenant_id, user_id), []))

    async def read_summaries(self, tenant_id: str, session_id: str) -> list[MemoryRecord]:
        return list(self._summaries.get((tenant_id, session_id), []))

    async def remove_l1(self, records: list[MemoryRecord]) -> None:
        to_remove = set(records)
        keys = {(record.tenant_id, record.session_id) for record in records}
        for key in keys:
            bucket = self._l1.get(key)
            if bucket is None:
                continue
            self._l1[key] = [record for record in bucket if record not in to_remove]


class MemoryManager:
    def __init__(
        self,
        store: SessionMemoryStore,
        *,
        policy: MemoryPolicy | None = None,
        summarizer_registry: SummarizerRegistryProtocol | None = None,
    ) -> None:
        self._store = store
        self._policy = policy or MemoryPolicy()
        # ADR-MEM-001：compaction 经 Summarizer registry resolve（默认只挂确定性截断）
        self._summarizer_registry = summarizer_registry or default_summarizer_registry()
        self._l0: dict[str, list[MemoryRecord]] = {}
        self._flushed_counts: dict[str, int] = {}

    def l0_messages(self, execution_id: str) -> list[MemoryRecord]:
        return list(self._l0.get(execution_id, []))

    async def add_message(
        self,
        context: RuntimeContext,
        role: str,
        content: str,
    ) -> MemoryRecord:
        record = _memory_record(context, role, content)
        records = self._l0.setdefault(context.snapshot.execution_id, [])
        records.append(record)
        if self._should_flush(records):
            await self._flush_new_records(context)
        return record

    async def read_session_context(self, context: RuntimeContext) -> list[MemoryRecord]:
        return await self._store.read_l1(context.snapshot.tenant_id, context.request.session_id)

    async def finish_execution(self, context: RuntimeContext) -> None:
        await self._flush_new_records(context)
        self._l0.pop(context.snapshot.execution_id, None)
        self._flushed_counts.pop(context.snapshot.execution_id, None)

    async def compact_context(self, context: RuntimeContext) -> CompactionResult:
        messages = await self._context_messages(context)
        retain = self._policy.retain_latest_turns
        older, raw = messages[:-retain], messages[-retain:]
        result = await self._summarize_records(context, older) if older else None
        summary = result.content if result else ""
        if summary:
            await self._store.append_summary(_memory_record(context, "summary", summary))
            # 已被摘要的 L1 记录删除，L1 与 L0 都截断，避免压缩后存储仍无界增长。
            await self._store.remove_l1(older)
            await self._drop_from_l0(context.snapshot.execution_id, older)
        context.emit("memory.compacted", {"summary_tokens": _estimate_tokens(summary)})
        return CompactionResult(
            raw_messages=raw,
            summary=summary,
            source_range_hash=result.source_range_hash if result else "",
        )

    async def _summarize_records(
        self, context: RuntimeContext, records: list[MemoryRecord]
    ) -> SummaryResult:
        """经 Summarizer registry resolve 调 SPI（ADR-MEM-001 替换假 `_summarize`）。

        优先 model summarizer；超时/异常降级 deterministic 截断 fallback，
        不静默吞（trace 事件 + 结构化日志，E-02）；registry 未命中 model 时
        直接走 deterministic（无 model 配置的正常路径，非降级）。
        """
        token_budget = self._policy.max_context_tokens
        summarizer_id = next(
            (
                candidate
                for candidate in (SUMMARIZER_MODEL, SUMMARIZER_DETERMINISTIC)
                if candidate in _registered_summarizer_ids(self._summarizer_registry)
            ),
            None,
        )
        if summarizer_id is None:
            raise RuntimeError("no summarizer registered (need model or deterministic)")
        summarizer = self._summarizer_registry.resolve(summarizer_id)
        try:
            return await summarizer.summarize(records, token_budget=token_budget)
        except Exception as exc:  # noqa: BLE001 — compaction 降级必须兜底，下方 trace+log 不静默吞
            error_type = type(exc).__name__
            emit_memory_event_log(
                event="memory.summarizer.fallback",
                tenant_id=context.snapshot.tenant_id,
                trace_id=context.snapshot.trace_id,
                execution_id=context.snapshot.execution_id,
                level="warning",
                detail=f"{error_type}: {exc}",
            )
            context.emit(
                "memory.summarizer_fallback",
                {"from": summarizer_id, "fallback": SUMMARIZER_DETERMINISTIC, "error_type": error_type},
            )
            fallback = self._summarizer_registry.resolve(SUMMARIZER_DETERMINISTIC)
            return await fallback.summarize(records, token_budget=token_budget)

    async def maybe_compact(self, context: RuntimeContext) -> bool:
        """上下文超 max_context_tokens 时触发摘要压缩；返回是否压缩过。

        compact_context 此前是死代码（runtime 从不调用），叠加 token 估算对
        中文系统性低估，L1 在中文长会话中无界增长，最终每轮请求都因 provider
        context length exceeded 永久失败。由 AgentRuntime.run_step 在每轮建消息前调用。
        """
        messages = await self._context_messages(context)
        total = sum(record.tokens for record in messages)
        if total < self._policy.max_context_tokens:
            return False
        await self.compact_context(context)
        return True

    async def _drop_from_l0(self, execution_id: str, records: list[MemoryRecord]) -> None:
        current = self._l0.get(execution_id, [])
        if not current:
            return
        summarized = {_record_identity(record) for record in records}
        self._l0[execution_id] = [
            record for record in current if _record_identity(record) not in summarized
        ]

    async def _context_messages(self, context: RuntimeContext) -> list[MemoryRecord]:
        persisted = await self._store.read_l1(context.snapshot.tenant_id, context.request.session_id)
        current = self._l0.get(context.snapshot.execution_id, [])
        seen: set[tuple[object, ...]] = set()
        merged: list[MemoryRecord] = []
        for record in [*persisted, *current]:
            # summary 记录独立存储，不参与后续压缩（避免摘要被重复摘要）。
            if record.role == "summary":
                continue
            key = _record_identity(record)
            if key not in seen:
                seen.add(key)
                merged.append(record)
        return merged

    async def _flush_new_records(self, context: RuntimeContext) -> None:
        execution_id = context.snapshot.execution_id
        records = self._l0.get(execution_id, [])
        already_flushed = self._flushed_counts.get(execution_id, 0)
        new_records = records[already_flushed:]
        if not new_records:
            return
        # ADR-MEM-001：双写修复——flush 只写 L1（session raw）。L2 legacy
        # user-raw 停止无脑双写（raw 会话翻倍进 user-level = 反模式）；legacy
        # L2 数据迁移/删除延后 Phase 2 TASK-M202。
        await self._store.append_l1(new_records)
        self._flushed_counts[execution_id] = len(records)
        context.emit("memory.flushed", {"records": len(new_records)})

    def _should_flush(self, records: list[MemoryRecord]) -> bool:
        total_tokens = sum(record.tokens for record in records)
        threshold = self._policy.max_context_tokens * self._policy.flush_threshold_ratio
        return total_tokens >= threshold


def _record_identity(record: MemoryRecord) -> tuple[object, ...]:
    return (
        record.tenant_id,
        record.user_id,
        record.session_id,
        record.execution_id,
        record.role,
        record.content,
        record.tokens,
    )


def _memory_record(context: RuntimeContext, role: str, content: str) -> MemoryRecord:
    return MemoryRecord(
        tenant_id=context.snapshot.tenant_id,
        user_id=context.snapshot.user_id,
        session_id=context.request.session_id,
        execution_id=context.snapshot.execution_id,
        role=role,
        content=content,
        tokens=_estimate_tokens(content),
    )


def _estimate_tokens(content: str) -> int:
    # 拉丁文沿用按词计（split）；CJK 无空格分词，此前整段中文计为 1 token，
    # 导致 flush/compact 永不触发、L1 无界增长 → provider context length
    # exceeded（中文场景的必然崩溃，见 FEAT-22/23）。修正：CJK 按单字计
    # （≈1 token/字，略过估但远胜低估；过估早 flush 不丢数据）。
    if not content:
        return 1
    word_tokens = len(content.split())
    cjk_chars = sum(1 for char in content if _is_cjk(ord(char)))
    return max(1, word_tokens + cjk_chars)


_CJK_RANGES = (
    (0x1100, 0x11FF),    # Hangul Jamo
    (0x2E80, 0x9FFF),    # CJK Radicals / Unified Ideographs
    (0xA000, 0xA4FF),    # Yi
    (0xAC00, 0xD7AF),    # Hangul Syllables
    (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
    (0xFE30, 0xFE4F),    # CJK Compatibility Forms
    (0xFF00, 0xFFEF),    # Fullwidth / Halfwidth
    (0x3000, 0x30FF),    # CJK Symbols / Hiragana / Katakana
    (0x20000, 0x2FA1F),  # CJK Extensions A–F
)


def _is_cjk(codepoint: int) -> bool:
    return any(low <= codepoint <= high for low, high in _CJK_RANGES)


def _registered_summarizer_ids(registry: SummarizerRegistryProtocol) -> set[str]:
    """registry 可选暴露 summarizer_ids（SummarizerRegistry 实现）；缺失时仅探测已知 id。"""
    ids = getattr(registry, "summarizer_ids", None)
    if callable(ids):
        return set(ids())
    return {
        candidate
        for candidate in (SUMMARIZER_MODEL, SUMMARIZER_DETERMINISTIC)
        if _can_resolve(registry, candidate)
    }


def _can_resolve(registry: SummarizerRegistryProtocol, summarizer_id: str) -> bool:
    try:
        registry.resolve(summarizer_id)
    except Exception:  # noqa: BLE001 — 探测性 resolve，任何失败都视为未注册
        return False
    return True
