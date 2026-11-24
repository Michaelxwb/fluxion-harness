"""Runtime compactor: summary persistence, material interval and cache."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import pytest
import sqlalchemy as sa
from muad_agent_core.context.plan import ContextPlan, render_context
from muad_agent_core.context.summary import SummaryFields
from muad_agent_core.model import ModelMessage, ModelRole, ModelToolCall
from muad_agent_runtime.application.context_compaction import RuntimeContextCompactor
from muad_agent_runtime.application.context_events import (
    CONTEXT_COMPACTED_EVENT,
    CONTEXT_SUMMARY_EVENT,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, Conversation
from muad_contracts.platform_settings import CompactionSettings, SummarySettings

from agent_runtime.conftest import TenantContext


def _fields(goal: str = "compressed goal") -> SummaryFields:
    return SummaryFields(
        user_goal=goal,
        constraints=(),
        progress=(),
        open_items=(),
        artifacts=(),
    )


def _plan(*, material_end: int = 4) -> ContextPlan:
    older = tuple(
        (
            ModelMessage(
                role=ModelRole.USER if index % 2 else ModelRole.ASSISTANT,
                content=f"old-{index}",
                source_event_seq=index,
            ),
        )
        for index in range(1, 7)
    )
    current = ModelMessage(
        role=ModelRole.USER,
        content="current",
        message_id="current",
        source_event_id="current-event",
        source_event_seq=99,
    )
    working = (
        (
            ModelMessage(
                role=ModelRole.ASSISTANT,
                content="",
                tool_calls=(ModelToolCall("c1", "echo", {}),),
                source_event_seq=100,
            ),
            ModelMessage(
                role=ModelRole.TOOL,
                content="tool result",
                tool_call_id="c1",
                source_event_seq=101,
            ),
        ),
    )
    return ContextPlan(
        prefix=(ModelMessage(role=ModelRole.SYSTEM, content="[历史摘要] old"),),
        older=older,
        pinned=((current,),),
        working=working,
        covered_seq=0,
        material_end_seq=material_end,
        head_end_seq=2,
        earlier_records=1,
    )


def _settings() -> CompactionSettings:
    return CompactionSettings(
        history_budget_tokens=100_000,
        summary=SummarySettings(enabled=True, model_ref="probe-model"),
    )


async def _conversation(tenant: TenantContext) -> Conversation:
    async with get_session_factory()() as session:
        row = Conversation(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            last_seq=0,
        )
        session.add(row)
        await session.commit()
    return row


async def _event_types(conversation_id: uuid.UUID) -> list[str]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            sa.select(CanonicalEvent.event_type).where(
                CanonicalEvent.conversation_id == conversation_id
            )
        )
        return list(rows)


@pytest.mark.asyncio
async def test_summary_persists_material_coverage_and_reuses_cache(
    tenant: TenantContext, database_guard: None, tmp_path
) -> None:
    conversation = await _conversation(tenant)
    calls: list[tuple[ModelMessage, ...]] = []

    async def summarize(material: Sequence[ModelMessage]) -> SummaryFields | None:
        calls.append(tuple(material))
        return _fields()

    compactor = RuntimeContextCompactor(
        settings=_settings(),
        tenant_id=tenant.tenant_id,
        run_id=uuid.uuid4(),
        conversation_id=conversation.id,
        artifact_root=tmp_path / "artifacts",
        summary_runner=summarize,
        session_factory=get_session_factory,
    )

    first = await compactor.compact_plan(_plan())
    second = await compactor.compact_plan(_plan())

    assert len(calls) == 1, "same material reuses the cached summary"
    assert [str(message.content) for message in calls[0]] == [
        "[历史摘要] old",
        "old-1",
        "old-2",
        "old-3",
        "old-4",
    ]
    assert render_context(first) == render_context(second)
    outbound = [str(message.content) for message in render_context(first)]
    assert "old-5" in outbound and "old-6" in outbound
    assert "current" in outbound

    async with get_session_factory()() as session:
        summary = await session.scalar(
            sa.select(CanonicalEvent).where(
                CanonicalEvent.conversation_id == conversation.id,
                CanonicalEvent.event_type == CONTEXT_SUMMARY_EVENT,
            ).order_by(CanonicalEvent.seq.desc())
        )
        assert summary is not None
        assert summary.payload_json["covers_up_to_seq"] == 4
        assert summary.payload_json["transcript_artifact_id"]
    assert CONTEXT_COMPACTED_EVENT in await _event_types(conversation.id)


@pytest.mark.asyncio
async def test_summary_failure_falls_back_without_writing_summary(
    tenant: TenantContext, database_guard: None, tmp_path
) -> None:
    conversation = await _conversation(tenant)

    async def summarize(_material: Sequence[ModelMessage]) -> SummaryFields | None:
        raise RuntimeError("summary down")

    compactor = RuntimeContextCompactor(
        settings=_settings(),
        tenant_id=tenant.tenant_id,
        run_id=uuid.uuid4(),
        conversation_id=conversation.id,
        artifact_root=tmp_path / "artifacts",
        summary_runner=summarize,
        session_factory=get_session_factory,
    )

    compacted = await compactor.compact_plan(_plan())

    assert compacted.covered_seq == 0
    assert CONTEXT_SUMMARY_EVENT not in await _event_types(conversation.id)
    assert any(
        "[历史裁剪]" in str(message.content)
        for group in compacted.older
        for message in group
    )
