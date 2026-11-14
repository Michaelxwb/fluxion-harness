"""TASK-007: the single loader keeps the v2 lane current and never freezes row versions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from muad_agent_runtime.application.memory_context import MemoryContextLoader
from muad_agent_runtime.application.memory_queries import MemoryCandidateRowV2, MemorySnapshotV2
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
    return MemoryCandidateRowV2(
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
    cache = _Cache(MemorySnapshotV2(None, 7, 0, NOW, (_row("b", version=2),)))
    loader = MemoryContextLoader(cache, settings=MemoryPolicySettings(), query="A 客户关注数据安全")
    component = await loader.refresh()
    assert component is not None
    assert component.snapshot_revision == 7
    assert component.text is None
    assert loader.resolution is not None and loader.resolution.entries == ()


async def test_loader_does_not_freeze_row_ttl():
    expired = _row("b", expires_at=datetime(2026, 11, 13, tzinfo=UTC))
    cache = _Cache(MemorySnapshotV2(None, 7, 0, NOW, (expired,)))
    loader = MemoryContextLoader(cache, settings=MemoryPolicySettings(), query="B 客户数据安全")
    component = await loader.refresh()
    assert component is not None
    assert component.text is None
    assert loader.resolution is not None and loader.resolution.entries == ()
