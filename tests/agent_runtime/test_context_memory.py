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
                # assistant 回合：声明了 c1（含思维链）。缺了它，下面的 TOOL_CALL 就是
                # 「孤儿 tool 消息」，历史重建会整条跳过（供应商会拒绝这种历史）。
                CanonicalEvent(
                    tenant_id=TENANT,
                    conversation_id=CONV_ID,
                    run_id=RUN_ID,
                    seq=2,
                    event_type="ASSISTANT_TURN",
                    payload_json={
                        "text": "",
                        "reasoning_content": "REASONING-CHUNK",
                        "tool_calls": [{"id": "c1", "name": "search", "arguments": {"q": "x"}}],
                    },
                    stream_type="assistant.turn",
                ),
                CanonicalEvent(
                    tenant_id=TENANT,
                    conversation_id=CONV_ID,
                    run_id=RUN_ID,
                    seq=3,
                    event_type="TOOL_CALL",
                    payload_json={"tool_name": "search", "tool_call_id": "c1"},
                    stream_type="tool.started",
                    artifact_id=artifact_id,
                ),
                CanonicalEvent(
                    tenant_id=TENANT,
                    conversation_id=CONV_ID,
                    run_id=RUN_ID,
                    seq=4,
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
    # 清理必须覆盖**对照租户** `{TENANT}-other`：那是为验证跨租户隔离而种的"别人的数据"，
    # 写入范围大于清理范围会让它每跑一次残留 2 行（2026-10-01 实测，永久累积）。
    tenants = (TENANT, f"{TENANT}-other")
    async with get_session_factory()() as session:
        await session.execute(
            Artifact.__table__.delete().where(Artifact.tenant_id.in_(tenants))
        )
        await session.execute(
            UserMemory.__table__.delete().where(UserMemory.tenant_id.in_(tenants))
        )
        await session.execute(
            CanonicalEvent.__table__.delete().where(CanonicalEvent.tenant_id.in_(tenants))
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

    # assistant 回合连 tool_calls 与思维链一起进历史：思考模式要求带 tool_calls 的消息
    # 必须回传 reasoning_content，否则整条请求被供应商拒绝
    turn = next(m for m in request.messages if m.tool_calls)
    assert [call.id for call in turn.tool_calls] == ["c1"]
    assert turn.reasoning_content == "REASONING-CHUNK"
    assert [m.tool_call_id for m in request.messages if m.tool_call_id is not None] == ["c1"]

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
    # 裁剪不修改 DB（种入 4 条：USER_MESSAGE / ASSISTANT_TURN / TOOL_CALL / ASSISTANT_MESSAGE）
    assert await _count_events() == 4


async def test_orphan_tool_messages_are_dropped_from_history() -> None:
    """没有前置 assistant 回合的 tool 消息**不得**进历史。

    回归（2026-10-01 排查）：`ASSISTANT_TURN` 上线前的存量会话里，tool 消息前面没有
    assistant `tool_calls`，供应商会拒绝**整个**请求 —— 实测原话：`Messages with role
    'tool' must be a response to a preceding message with 'tool_calls'`。用户侧表现为
    「会话用过一次工具之后就永久不可用，只能 /new 逃生」。历史重建改为「成对才收」后，
    孤儿一律跳过 ⇒ 存量会话无需迁移即可自愈（代价是丢那几轮的工具上下文）。
    """
    tenant = f"orphan-{uuid.uuid4()}"
    conv_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conv_id,
                tenant_id=tenant,
                user_id=USER_ID,
                agent_id=uuid.uuid4(),
                status="ACTIVE",
                last_seq=3,
            )
        )
        session.add_all(
            [
                CanonicalEvent(
                    tenant_id=tenant,
                    conversation_id=conv_id,
                    run_id=RUN_ID,
                    seq=1,
                    event_type="USER_MESSAGE",
                    payload_json={"text": "hello"},
                ),
                # 孤儿：没有任何 ASSISTANT_TURN 声明过 c1
                CanonicalEvent(
                    tenant_id=tenant,
                    conversation_id=conv_id,
                    run_id=RUN_ID,
                    seq=2,
                    event_type="TOOL_CALL",
                    payload_json={"tool_name": "search", "tool_call_id": "c1"},
                ),
                CanonicalEvent(
                    tenant_id=tenant,
                    conversation_id=conv_id,
                    run_id=RUN_ID,
                    seq=3,
                    event_type="ASSISTANT_MESSAGE",
                    payload_json={"text": "done"},
                ),
            ]
        )
        await session.commit()

    try:
        builder = DbBackedContextBuilder(session_factory=get_session_factory)
        request = await builder.build(
            ContextInput(
                model_id="gpt-4o-mini",
                instructions="be helpful",
                conversation_id=conv_id,
                tenant_id=tenant,
                user_id=USER_ID,
                budget_messages=20,
            ),
        )
        roles = [str(message.role) for message in request.messages]
        assert roles == ["system", "user", "assistant"], roles
        assert all(message.tool_call_id is None for message in request.messages)
        assert all(not message.tool_calls for message in request.messages)
    finally:
        async with get_session_factory()() as session:
            await session.execute(
                sa.delete(CanonicalEvent).where(CanonicalEvent.tenant_id == tenant)
            )
            await session.execute(sa.delete(Conversation).where(Conversation.id == conv_id))
            await session.commit()
