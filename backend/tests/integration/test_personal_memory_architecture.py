"""ADR-MEM-001 TASK-004：PersonalMemoryRetriever + architecture-test（E-01 读侧 + E-03 写侧）。

真实边界（契约声明）：
- S-04：真实 `PersonalMemoryRetriever` → 真实 `SemanticStoreProvider` 实现
  （TableBackedSemanticStore 经 PersonalMemoryStore 查真实 personal_memory 表，
  非 mock）→ 经真实 `MemoryLearner.commit` 写入。
- E-01：AST 静态依赖扫描（真实模块源码）。
- E-03：inspect/AST 结构检查 + 真实 compaction 行为断言。

RED 约定（cf-task:start #7）：
- S-04 真实 RED：`PersonalMemoryRetriever` 未实现 → function-level import 抛
  ImportError（仅该场景失败，见测试内注释）。
- E-01/E-03 为静态守卫：硬边界已由 TASK-002/003 实现（memory.py 不引用
  personal_memory；`_insert` 私有且仅 commit 调用），守卫落地时 green-before；
  各守卫配 synthetic violating source 证明可捕获违规（非 vacuous），GREEN 后
  防回归 + CI 阻断。
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fluxion.plugins.contracts import SemanticStoreError, SemanticStoreProvider
from fluxion.registry import RegistryStore
from fluxion.registry.schema import metadata, session_memory
from fluxion.runtime import AgentRuntime, RequestContext
from fluxion.runtime.memory import MemoryPolicy
from fluxion.runtime.memory_sql import SQLSessionMemoryStore
from fluxion.runtime.personal_memory import (
    ConsentDecision,
    MemoryCandidate,
    MemoryLearner,
    MemoryType,
    PersonalMemoryStore,
    PolicyDecision,
)
from fluxion.runtime.resolver import ExecutionSnapshotBuilder, ResourceResolver
from tests.runtime_helpers import seed_runtime_profile

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME_ROOT = _BACKEND_ROOT / "src" / "fluxion" / "runtime"
_PERSONAL_MEMORY_PATH = _RUNTIME_ROOT / "personal_memory.py"

_ALLOW_POLICY = PolicyDecision(allowed=True)
_ALLOW_CONSENT = ConsentDecision(allowed=True)


@pytest.fixture
async def memory_engine() -> AsyncGenerator[AsyncEngine, None]:
    """真实 async SQLite engine：personal_memory / session_memory 等真实建表。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


def _candidate(content: str, memory_type: MemoryType) -> MemoryCandidate:
    return MemoryCandidate(
        tenant_id="tenant-a",
        user_id="user-a",
        memory_type=memory_type,
        content=content,
        source_session_id="session-a",
        source_range_hash="a" * 64,
    )


async def _commit_personal_entries(store: PersonalMemoryStore) -> None:
    """经唯一写入口 MemoryLearner.commit 写入三条 personal memory。"""
    learner = MemoryLearner(store)
    for candidate in (
        _candidate("user prefers concise answers", MemoryType.SEMANTIC),
        _candidate("user prefers concise and structured replies", MemoryType.SEMANTIC),
        _candidate("user visited Tokyo in 2024", MemoryType.EPISODIC),
    ):
        result = await learner.commit(
            candidate,
            policy_decision=_ALLOW_POLICY,
            consent=_ALLOW_CONSENT,
            learning_enabled=True,
        )
        assert result.committed is True


async def _seed_session_context_summary(engine: AsyncEngine, content: str) -> None:
    """同 tenant/user 的 session_memory 里 seed 一条 SessionContextSummary。

    内容特意包含查询词 "concise"：若读侧越界（cross-read summary），
    S-04 的检索结果会泄漏出这条内容。
    """
    async with engine.begin() as connection:
        await connection.execute(
            insert(session_memory).values(
                tenant_id="tenant-a",
                user_id="user-a",
                session_id="session-a",
                execution_id="exec-seed",
                role="summary",
                content=content,
                tokens=8,
                level="session_context_summary",
                created_at=datetime.now(UTC),
            )
        )


class TableBackedSemanticStore:
    """真实 SemanticStoreProvider：经 PersonalMemoryStore 查真实 personal_memory 表。

    Phase 0 词汇匹配（pgvector 向量检索是 Phase 1 FEAT-17 范围）；
    store() 拒绝直写——personal memory 写入唯一入口是 MemoryLearner.commit（E-03）。
    """

    def __init__(self, store: PersonalMemoryStore) -> None:
        self._store = store

    async def store(
        self,
        tenant_id: str,
        user_id: str,
        record: dict[str, object],
        timeout_ms: int = 30_000,
    ) -> None:
        raise SemanticStoreError("personal memory writes must go through MemoryLearner.commit")

    async def recall(
        self,
        tenant_id: str,
        user_id: str,
        query: str,
        top_k: int = 5,
        timeout_ms: int = 30_000,
    ) -> list[dict[str, object]]:
        entries = await self._store.list_entries(tenant_id, user_id)
        lowered = query.lower()
        matched = [entry for entry in entries if lowered in entry.content.lower()]
        return [_record_from_entry(entry) for entry in matched[:top_k]]

    async def search(
        self,
        tenant_id: str,
        user_id: str,
        filters: dict[str, object],
        timeout_ms: int = 30_000,
    ) -> list[dict[str, object]]:
        entries = await self._store.list_entries(tenant_id, user_id)
        memory_type = filters.get("memory_type")
        if memory_type is not None:
            wanted = (
                memory_type.value if isinstance(memory_type, MemoryType) else str(memory_type)
            )
            entries = [entry for entry in entries if entry.memory_type.value == wanted]
        return [_record_from_entry(entry) for entry in entries]


def _record_from_entry(entry) -> dict[str, object]:
    return {
        "id": entry.id,
        "tenant_id": entry.tenant_id,
        "user_id": entry.user_id,
        "memory_type": entry.memory_type,
        "content": entry.content,
        "source_session_id": entry.source_session_id,
        "source_range_hash": entry.source_range_hash,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


# --- S-04：经 SemanticStore SPI 检索 personal memory ---


async def test_s04_recall_via_semantic_store_returns_personal_entries_only(
    memory_engine: AsyncEngine,
) -> None:
    # function-level import：RED 阶段 PersonalMemoryRetriever 未实现，仅本场景失败
    from fluxion.runtime.personal_memory import PersonalMemoryRetriever

    store = PersonalMemoryStore(memory_engine)
    await _commit_personal_entries(store)
    await _seed_session_context_summary(
        memory_engine, "session summary mentioning concise answers"
    )

    provider = TableBackedSemanticStore(store)
    # 真实 SPI 契约符合性（runtime_checkable Protocol）
    assert isinstance(provider, SemanticStoreProvider)
    retriever = PersonalMemoryRetriever(provider)

    results = await retriever.recall("tenant-a", "user-a", "concise", top_k=5)

    # 只命中 personal 的两条 semantic 条目（真实表查询，按 id 升序）
    assert [entry.content for entry in results] == [
        "user prefers concise answers",
        "user prefers concise and structured replies",
    ]
    assert all(entry.memory_type is MemoryType.SEMANTIC for entry in results)
    assert all(entry.tenant_id == "tenant-a" for entry in results)
    # 不读 session_memory 的 SessionContextSummary：seed 的 summary 内容不泄漏
    assert all("session summary" not in entry.content for entry in results)

    episodic = await retriever.recall("tenant-a", "user-a", "Tokyo", top_k=5)
    assert [entry.memory_type for entry in episodic] == [MemoryType.EPISODIC]


async def test_s04_recall_respects_top_k_and_tenant_scope(
    memory_engine: AsyncEngine,
) -> None:
    from fluxion.runtime.personal_memory import PersonalMemoryRetriever

    store = PersonalMemoryStore(memory_engine)
    await _commit_personal_entries(store)
    retriever = PersonalMemoryRetriever(TableBackedSemanticStore(store))

    assert len(await retriever.recall("tenant-a", "user-a", "concise", top_k=1)) == 1
    # tenant 隔离（NFR-SEC-01）：跨 tenant 检索为空
    assert await retriever.recall("tenant-b", "user-a", "concise", top_k=5) == []


# --- E-01：读侧静态守卫（PersonalMemoryRetriever 不 import/read SessionContextSummary）---


def _imported_modules(source: str) -> set[str]:
    """AST 扫描源码的 ImportFrom.module + Import.names。"""
    mods: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
    return mods


def _schema_imported_names(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module == "fluxion.registry.schema":
            names.update(alias.name for alias in node.names)
    return names


def test_e01_retriever_module_does_not_import_or_read_session_context_summary() -> None:
    source = _PERSONAL_MEMORY_PATH.read_text(encoding="utf-8")

    # SessionMemoryStore（含 SessionContextSummary 读面）所在模块不得被 import
    assert "fluxion.runtime.memory" not in _imported_modules(source)
    assert "fluxion.runtime.memory_sql" not in _imported_modules(source)
    # 不得直接 import session_memory 表（summary 桶所在表）
    assert "session_memory" not in _schema_imported_names(source)
    # 不得出现 summary level 字面量（防裸 SQL 直查 summary 桶）
    assert "session_context_summary" not in source


def test_e01_guard_catches_violating_source() -> None:
    """teeth-proof：守卫能捕获违规源码，证明上一条断言非 vacuous。"""
    violating = (
        "from fluxion.runtime.memory import SessionMemoryStore\n"
        "from fluxion.registry.schema import session_memory\n"
        '_LEVEL = "session_context_summary"\n'
    )
    assert "fluxion.runtime.memory" in _imported_modules(violating)
    assert "session_memory" in _schema_imported_names(violating)
    assert "session_context_summary" in violating


# --- E-03：写侧 enforcement（绕过 MemoryLearner.commit 直写被阻断）---


def test_e03_store_public_surface_is_user_visible_only() -> None:
    """公开面只允许 查看/纠正/删除；新增写入唯一入口是 MemoryLearner.commit。"""
    public = {
        name
        for name, _ in inspect.getmembers(PersonalMemoryStore, inspect.isfunction)
        if not name.startswith("_")
    }
    assert public == {"list_entries", "update_content", "delete"}


def _insert_callers(source: str) -> set[tuple[str, str]]:
    """收集 `._insert(` 调用所在的 (class, method) 链。"""
    callers: set[tuple[str, str]] = set()
    for class_node in ast.walk(ast.parse(source)):
        if not isinstance(class_node, ast.ClassDef):
            continue
        for method in class_node.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(method):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_insert"
                ):
                    callers.add((class_node.name, method.name))
    return callers


def test_e03_insert_is_only_called_inside_memory_learner_commit() -> None:
    source = _PERSONAL_MEMORY_PATH.read_text(encoding="utf-8")
    assert _insert_callers(source) == {("MemoryLearner", "commit")}


def test_e03_insert_call_scan_catches_bypass() -> None:
    """teeth-proof：绕过 commit 的直写调用会被扫描捕获。"""
    violating = (
        "class Writer:\n"
        "    async def write(self, c):\n"
        "        await self._store._insert(c)\n"
    )
    assert _insert_callers(violating) == {("Writer", "write")}


def test_e03_session_side_modules_do_not_reference_personal_memory() -> None:
    """summarizer / session memory 侧不得引用 personal_memory 或 MemoryLearner。"""
    for name in ("summarizer.py", "memory.py", "memory_sql.py"):
        source = (_RUNTIME_ROOT / name).read_text(encoding="utf-8")
        assert "fluxion.runtime.personal_memory" not in _imported_modules(source), name
        assert "MemoryLearner" not in source, name


async def test_e03_compaction_does_not_auto_commit_summary_into_personal_memory(
    memory_engine: AsyncEngine,
    sqlite_store: RegistryStore,
) -> None:
    """行为证据：真实 compaction 产出 summary 后，personal_memory 表零行。

    summary 只回 session compaction（§4.6 行 276），不 auto-commit 进
    UserProfile/personal memory。
    """
    await seed_runtime_profile(sqlite_store)
    runtime = AgentRuntime(
        snapshot_builder=ExecutionSnapshotBuilder(ResourceResolver(sqlite_store)),
        memory_store=SQLSessionMemoryStore(memory_engine),
        memory_policy=MemoryPolicy(max_context_tokens=12, retain_latest_turns=2),
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

    compacted = await runtime.memory.compact_context(context)

    # compaction 确实发生并产出了 summary（session 侧）
    assert compacted.summary
    session_store = SQLSessionMemoryStore(memory_engine)
    summaries = await session_store.read_summaries("tenant-a", "session-a")
    assert summaries and summaries[-1].content == compacted.summary
    # 但 personal_memory 零行：SessionContextSummary 未被 auto-commit
    personal = PersonalMemoryStore(memory_engine)
    assert await personal.list_entries("tenant-a", "user-a") == []
