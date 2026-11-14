"""TASK-007: the single loader keeps the v2 lane current and never freezes row versions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from muad_agent_runtime.application.memory_context import MemoryContextLoader
from muad_agent_runtime.application.memory_queries import MemoryCandidateRow, MemorySnapshot
from muad_contracts.platform_settings import MemoryPolicySettings

NOW = datetime(2026, 11, 14, tzinfo=UTC)


class _Cache:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.invalidated = False

    async def read_for_injection(self, *, max_items, max_bytes):
        return self.snapshot

    def invalidate(self):
        self.invalidated = True


def _row(key, *, version=1, expires_at=None):
    return MemoryCandidateRow(
        id=uuid4(),
        memory_key=key,
        memory_kind="NOTE",
        predicate="general",
        scope_state="ABSENT",
        scope={},
        content={"text": "data security"},
        title="B 客户关注数据安全",
        summary="",
        tags=("entity:customer:B", "topic:data-security"),
        cardinality="SET",
        priority="OPTIONAL",
        author="AGENT",
        authority="AGENT_INFERRED",
        version=version,
        enabled=True,
        expires_at=expires_at,
        last_confirmed_at=None,
    )


async def test_loader_exposes_snapshot_revision_and_keeps_unmatched_entities_out():
    cache = _Cache(MemorySnapshot(None, 7, 0, NOW, (_row("b", version=2),)))
    loader = MemoryContextLoader(cache, settings=MemoryPolicySettings(), query="A 客户关注数据安全")
    component = await loader.refresh()
    assert component is not None
    assert component.snapshot_revision == 7
    assert component.text is None
    assert loader.resolution is not None and loader.resolution.entries == ()


async def test_loader_does_not_freeze_row_ttl():
    expired = _row("b", expires_at=datetime(2026, 11, 13, tzinfo=UTC))
    cache = _Cache(MemorySnapshot(None, 7, 0, NOW, (expired,)))
    loader = MemoryContextLoader(cache, settings=MemoryPolicySettings(), query="B 客户数据安全")
    component = await loader.refresh()
    assert component is not None
    assert component.text is None
    assert loader.resolution is not None and loader.resolution.entries == ()


def _scoped(key, audience, *, value):
    return MemoryCandidateRow(
        id=uuid4(),
        memory_key=key,
        memory_kind="PREFERENCE",
        predicate="report_format",
        scope_state="EXPLICIT" if audience else "ABSENT",
        scope={"audience": audience} if audience else {},
        content={"value": value},
        title="报告格式",
        summary="",
        tags=(),
        cardinality="SINGLE",
        priority="REQUIRED",
        author="USER",
        authority="USER_EXPLICIT",
        version=1,
        enabled=True,
        expires_at=None,
        last_confirmed_at=NOW,
    )


async def test_scope_inherits_from_recent_user_messages():
    rows = (_scoped("ext", "external", value="Word"), _scoped("int", "internal", value="PDF"))
    loader = MemoryContextLoader(
        _Cache(MemorySnapshot(None, 1, 0, NOW, rows)),
        settings=MemoryPolicySettings(),
        query="继续导出报告",
        recent_user_texts=("以后给客户的报告都用 Word",),
    )
    component = await loader.refresh()
    assert component is not None
    assert "Word" in (component.text or "") and "PDF" not in (component.text or "")


async def test_task_switch_clears_inherited_scope():
    rows = (_scoped("ext", "external", value="Word"), _scoped("int", "internal", value="PDF"))
    loader = MemoryContextLoader(
        _Cache(MemorySnapshot(None, 1, 0, NOW, rows)),
        settings=MemoryPolicySettings(),
        query="换个任务，随便聊聊",
        recent_user_texts=("以后给客户的报告都用 Word",),
    )
    component = await loader.refresh()
    assert component is not None and component.text is None


async def test_conflicting_scope_words_are_reported_as_ambiguous():
    rows = (_scoped("ext", "external", value="Word"), _scoped("int", "internal", value="PDF"))
    loader = MemoryContextLoader(
        _Cache(MemorySnapshot(None, 1, 0, NOW, rows)),
        settings=MemoryPolicySettings(),
        query="内部还是外部？",
    )
    component = await loader.refresh()
    assert component is not None and component.text is None
    assert loader.resolution is not None
    assert loader.resolution.clarification_reason == "SCOPE_AMBIGUOUS"


async def test_explicit_save_with_unknown_scope_is_rejected(async_tool_database):
    from muad_agent_runtime.application.memory_commands import MemoryWriteContext
    from muad_agent_runtime.application.memory_queries import MemoryQueries, MemoryReadScope
    from muad_agent_runtime.application.memory_service import MemoryService
    from muad_agent_runtime.application.memory_tools import MemoryToolSet
    from muad_api import AppError
    from muad_api.error_codes import ErrorCode
    from sqlalchemy.ext.asyncio import async_sessionmaker

    tenant, user = "unknown-scope", uuid4()
    scope = MemoryReadScope(tenant_id=tenant, user_id=user)
    cache = _Cache(MemorySnapshot(None, 0, 0, NOW, ()))
    cache.scope = scope
    loader = MemoryContextLoader(cache, settings=MemoryPolicySettings(), query="记住我的角色")
    tool_set = MemoryToolSet(
        service=MemoryService(async_sessionmaker(async_tool_database, expire_on_commit=False)),
        queries=MemoryQueries(async_sessionmaker(async_tool_database, expire_on_commit=False)),
        context=MemoryWriteContext(
            tenant_id=tenant, user_id=user, actor_type="USER", actor_id=str(user)
        ),
        loader=loader,
        confirmation=None,
    )
    with pytest.raises(AppError) as exc:
        await tool_set.remember(
            {
                "memory_kind": "FACT",
                "predicate": "role",
                "scope_state": "UNKNOWN",
                "content": {"value": "pm"},
                "title": "角色",
            },
            call_id="unknown-scope",
        )
    assert exc.value.code == ErrorCode.MEMORY_SCOPE_UNKNOWN
