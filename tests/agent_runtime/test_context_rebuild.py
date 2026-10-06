"""[E-01 / RULE-05] 压缩后的历史可由库确定性重建（FEAT-05/08）。

真实边界：真实 PostgreSQL 的 `runtime.canonical_event`。本文件的"重建"是**测试侧独立实现**
（裸 SQL + 文档口径），不是复用生产函数——否则就是拿代码验自己。
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from muad_agent_core.context.summary import SummaryArtifact, SummaryFields, summary_message
from muad_agent_runtime.application.context_builder import DbBackedContextBuilder
from muad_agent_runtime.application.context_events import (
    CONTEXT_SUMMARY_EVENT,
    record_summary,
)
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Artifact,
    CanonicalEvent,
    Conversation,
)

from agent_runtime.conftest import TenantContext

SUMMARY = {
    "user_goal": "别忘了我最初的诉求",
    "constraints": ["阈值按 UTF-8 字节"],
    "progress": ["旧工具结果已降级"],
    "open_items": ["摘要默认关"],
    "artifacts": [{"artifact_id": "art-0", "tool": "read_attachment", "note": "被省略的原文"}],
}


def _builder() -> DbBackedContextBuilder:
    return DbBackedContextBuilder(session_factory=get_session_factory)


async def _seed(tenant: TenantContext) -> tuple[uuid.UUID, uuid.UUID]:
    """造一段会话：3 条被摘要覆盖的旧事件 + 摘要事件 + 2 条摘要之后的原始事件。"""
    conversation = Conversation(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        user_id=tenant.platform_user_id,
        agent_id=tenant.agent_id,
        last_seq=0,
    )
    run_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(conversation)
        await session.flush()
        writer = EventWriter(session)
        for index in range(3):
            await writer.append(
                tenant_id=tenant.tenant_id,
                conversation_id=conversation.id,
                run_id=run_id,
                event_type="USER_MESSAGE",
                payload={"text": f"被压缩掉的旧诉求 {index}"},
            )
        covered_through = conversation.last_seq
        await record_summary(
            writer,
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            run_id=run_id,
            covers_up_to_seq=covered_through,
            summary=SUMMARY,
            transcript_artifact_id="t-1",
            bytes_before=40_000,
            bytes_after=800,
        )
        for text in ("摘要之后的追问 1", "摘要之后的追问 2"):
            await writer.append(
                tenant_id=tenant.tenant_id,
                conversation_id=conversation.id,
                run_id=run_id,
                event_type="USER_MESSAGE",
                payload={"text": text},
            )
        await session.commit()
    return conversation.id, run_id


async def _raw_rebuild(
    tenant: TenantContext, conversation_id: uuid.UUID
) -> list[str]:
    """测试侧独立重建（裸 SQL + 文档口径）：最新摘要作前缀，其后按 seq 顺序应用原始事件。"""
    async with get_session_factory()() as session:
        summary_row = await session.scalar(
            sa.select(CanonicalEvent)
            .where(
                CanonicalEvent.tenant_id == tenant.tenant_id,
                CanonicalEvent.conversation_id == conversation_id,
                CanonicalEvent.event_type == CONTEXT_SUMMARY_EVENT,
            )
            .order_by(CanonicalEvent.seq.desc())
            .limit(1)
        )
        covered = summary_row.payload_json["covers_up_to_seq"] if summary_row else 0
        rows = (
            await session.execute(
                sa.select(CanonicalEvent)
                .where(
                    CanonicalEvent.tenant_id == tenant.tenant_id,
                    CanonicalEvent.conversation_id == conversation_id,
                    CanonicalEvent.seq > covered,
                    CanonicalEvent.event_type == "USER_MESSAGE",
                )
                .order_by(CanonicalEvent.seq)
            )
        ).scalars().all()

    prefix = []
    if summary_row is not None:
        payload = summary_row.payload_json["summary"]
        # 手写字段映射（不调 `summary_from_payload`），确保这条断言独立于被测实现。
        prefix.append(
            summary_message(
                SummaryFields(
                    user_goal=payload["user_goal"],
                    constraints=tuple(payload["constraints"]),
                    progress=tuple(payload["progress"]),
                    open_items=tuple(payload["open_items"]),
                    artifacts=tuple(SummaryArtifact(**item) for item in payload["artifacts"]),
                )
            ).content
        )
    return prefix + [str(row.payload_json["text"]) for row in rows]


async def test_e01_rebuild_is_deterministic_and_matches_the_independent_oracle(
    tenant: TenantContext, database_guard: None
) -> None:
    conversation_id, _ = await _seed(tenant)

    first = await _builder().load_history(
        tenant_id=tenant.tenant_id, conversation_id=conversation_id, user_id=None
    )
    second = await _builder().load_history(
        tenant_id=tenant.tenant_id, conversation_id=conversation_id, user_id=None
    )

    # 同一份库、两个独立实例（换 Pod 的等价物）⇒ 逐字节相同。
    assert [message.content for message in first] == [message.content for message in second]
    assert [str(message.role) for message in first] == [str(message.role) for message in second]

    assert [message.content for message in first] == await _raw_rebuild(tenant, conversation_id)
    assert first[0].content.startswith("[历史摘要]"), "摘要作为权威历史排在最前"
    joined = "\n".join(str(message.content) for message in first)
    assert "被压缩掉的旧诉求" not in joined, "被摘要覆盖的原始事件不再进入装配"
    assert "摘要之后的追问 2" in joined, "覆盖边界之后的事件按 seq 顺序接着来"


async def test_e01_a_new_summary_takes_effect_on_the_next_rebuild(
    tenant: TenantContext, database_guard: None
) -> None:
    """覆盖范围推进后再重建 ⇒ 结果随之推进（证明前缀真的来自库里那份，不是进程内缓存）。"""
    conversation_id, run_id = await _seed(tenant)
    async with get_session_factory()() as session:
        latest_seq = await session.scalar(
            sa.select(sa.func.max(CanonicalEvent.seq)).where(
                CanonicalEvent.conversation_id == conversation_id
            )
        )
        await record_summary(
            EventWriter(session),
            tenant_id=tenant.tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            covers_up_to_seq=int(latest_seq or 0),
            summary={**SUMMARY, "user_goal": "推进后的目标"},
            transcript_artifact_id="t-2",
            bytes_before=50_000,
            bytes_after=900,
        )
        await session.commit()

    rebuilt = await _builder().load_history(
        tenant_id=tenant.tenant_id, conversation_id=conversation_id, user_id=None
    )

    assert "推进后的目标" in str(rebuilt[0].content), "取最新一份覆盖事件作前缀"
    assert "摘要之后的追问" not in "\n".join(str(message.content) for message in rebuilt)


async def test_rule05_summary_is_authoritative_and_transcript_is_only_an_archive(
    tenant: TenantContext, database_guard: None, tmp_path
) -> None:
    """RULE-05：重建用的是**库里的摘要**；transcript 是另存的逐字存档，两者不得互换。"""
    from muad_agent_core.model import ModelMessage, ModelRole
    from muad_agent_runtime.application.attachments.transcripts import TranscriptWriter

    conversation_id, _ = await _seed(tenant)
    rebuilt = await _builder().load_history(
        tenant_id=tenant.tenant_id, conversation_id=conversation_id, user_id=None
    )

    # 存档侧：逐字原文落在共享产物里，类型 TRANSCRIPT，且**不在** canonical_event 中。
    writer = TranscriptWriter(tmp_path / "artifacts")
    async with get_session_factory()() as session:
        reference = await writer.persist_with_session(
            session,
            tenant_id=tenant.tenant_id,
            conversation_id=conversation_id,
            run_id=uuid.uuid4(),
            task_id=None,
            messages=[ModelMessage(role=ModelRole.USER, content="被压缩掉的旧诉求 0")],
        )
        assert reference is not None
        artifact = await session.get(Artifact, uuid.UUID(reference["artifact_id"]))

    assert artifact is not None and artifact.artifact_type == "TRANSCRIPT"
    assert "被压缩掉的旧诉求 0" not in json.dumps(
        [row for row in rebuilt], default=str
    ), "摘要不作 verbatim 存档，逐字原文不进历史（进历史的是摘要）"

    # 互换即失真：把 transcript 的逐字原文当前缀，重建结果会变。
    swapped = [
        ModelMessage(role=ModelRole.SYSTEM, content="被压缩掉的旧诉求 0"),
        *rebuilt[1:],
    ]
    assert [message.content for message in swapped] != [
        message.content for message in rebuilt
    ], "摘要与 transcript 一旦互换，重建结果必然不同——二者不是同一种东西"

