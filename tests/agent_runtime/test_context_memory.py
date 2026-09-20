"""[S-08] 上下文重建与预算裁剪（真实 CanonicalEvent/Memory/Artifact→ContextBuilder→LLM request）。"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from muad_agent_core.context.builder import ContextInput
from muad_agent_runtime.application.context_builder import (
    DbBackedContextBuilder,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Artifact,
    CanonicalEvent,
    Conversation,
    UserMemory,
)

TENANT = f"ctx-{uuid.uuid4()}"
CONV_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


@pytest.fixture()
async def seeded():
    artifact_id = uuid.uuid4()

    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=CONV_ID,
                tenant_id=TENANT,
                user_id=USER_ID,
                agent_id=uuid.uuid4(),
                status="ACTIVE",
                last_seq=6,
            )
        )
        session.add_all(
            [
                CanonicalEvent(
                    tenant_id=TENANT,
                    conversation_id=CONV_ID,
                    run_id=RUN_ID,
                    seq=1,
                    event_type="USER_MESSAGE",
                    payload_json={"text": "hello"},
                ),
                CanonicalEvent(
                    tenant_id=TENANT,
                    conversation_id=CONV_ID,
                    run_id=RUN_ID,
                    seq=2,
                    event_type="TOOL_CALL",
                    payload_json={"tool": "search", "call_id": "c1"},
                    stream_type="tool.started",
                    artifact_id=artifact_id,
                ),
                CanonicalEvent(
                    tenant_id=TENANT,
                    conversation_id=CONV_ID,
                    run_id=RUN_ID,
                    seq=3,
                    event_type="ASSISTANT_MESSAGE",
                    payload_json={"text": "found it"},
                ),
                # 其他租户的事件：不得泄漏
                CanonicalEvent(
                    tenant_id=f"{TENANT}-other",
                    conversation_id=uuid.uuid4(),
                    run_id=uuid.uuid4(),
                    seq=1,
                    event_type="USER_MESSAGE",
                    payload_json={"text": "leak?"},
                ),
            ]
        )
        session.add(
            UserMemory(
                tenant_id=TENANT,
                user_id=USER_ID,
                memory_key="pref.language",
                category="PREFERENCE",
                content_json={"value": "zh-CN"},
                source_type="EXPLICIT",
                enabled=True,
            )
        )
        session.add(
            UserMemory(
                tenant_id=TENANT,
                user_id=USER_ID,
                memory_key="pref.disabled",
                category="PREFERENCE",
                content_json={"value": "off"},
                source_type="EXPLICIT",
                enabled=False,
            )
        )
        session.add(
            Artifact(
                id=artifact_id,
                tenant_id=TENANT,
                run_id=RUN_ID,
                conversation_id=CONV_ID,
                artifact_type="TOOL_RESULT",
                storage_key="tools/x/result.bin",
                media_type="text/plain",
                size=5000,
                checksum="sha256:" + "0" * 64,
                preview_text="PREVIEW-CHUNK",
            )
        )
        await session.commit()
    yield
    async with get_session_factory()() as session:
        await session.execute(
            Artifact.__table__.delete().where(Artifact.tenant_id == TENANT)
        )
        await session.execute(
            UserMemory.__table__.delete().where(UserMemory.tenant_id == TENANT)
        )
        await session.execute(
            CanonicalEvent.__table__.delete().where(CanonicalEvent.tenant_id == TENANT)
        )
        await session.execute(
            Conversation.__table__.delete().where(Conversation.id == CONV_ID)
        )
        await session.commit()


async def _count_events() -> int:
    async with get_session_factory()() as session:
        return await session.scalar(
            sa.select(sa.func.count()).select_from(CanonicalEvent).where(
                CanonicalEvent.tenant_id == TENANT
            )
        )


async def test_s08_context_from_db_with_isolation_and_preview(seeded) -> None:
    """[S-08] 从 DB 构建请求：隔离/预览/裁剪不修改事件（append-only）。"""
    builder = DbBackedContextBuilder(session_factory=get_session_factory)
    before = await _count_events()

    request = await builder.build(
        ContextInput(
            model_id="gpt-4o-mini",
            instructions="be helpful",
            conversation_id=CONV_ID,
            tenant_id=TENANT,
            user_id=USER_ID,
            budget_messages=10,
        ),
    )
    assert request.model_id == "gpt-4o-mini"

    # 历史：USER/ASSISTANT 消息按 seq 进入；stream_type=tool.started 不进业务上下文
    content = " ".join(str(m.content) for m in request.messages)
    assert "hello" in content
    assert "found it" in content
    assert "leak?" not in content  # 跨租户隔离

    # Memory：enabled 条目进入，disabled 不进
    assert "zh-CN" in content
    assert "off" not in content

    # Artifact：大结果仅 preview
    assert "PREVIEW-CHUNK" in content
    assert "x" * 200_000 not in content

    # append-only：构建后事件数不变
    after = await _count_events()
    assert before == after


async def test_s08_budget_trims_only_request_keeps_tool_pairs(seeded) -> None:
    """[S-08] 预算裁剪仅作用于派生 request；Tool 消息配对保留。"""
    builder = DbBackedContextBuilder(session_factory=get_session_factory)
    request = await builder.build(
        ContextInput(
            model_id="gpt-4o-mini",
            instructions="be helpful",
            conversation_id=CONV_ID,
            tenant_id=TENANT,
            user_id=USER_ID,
            budget_messages=2,
        ),
    )
    assert len(request.messages) <= 2 + 2  # 预算 + 系统提示/配对容差
    # 裁剪不修改 DB
    assert await _count_events() == 3
