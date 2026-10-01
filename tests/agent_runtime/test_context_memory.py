"""[S-08] 上下文重建与预算裁剪（真实 CanonicalEvent/Memory/Artifact→ContextBuilder→LLM request）。"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_core.context.builder import ContextInput
from muad_agent_runtime.application.context_builder import (
    MAX_INJECTED_BYTES,
    MAX_INJECTED_MEMORIES,
    DbBackedContextBuilder,
)
from muad_agent_runtime.application.memory_service import MemoryService
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Artifact,
    CanonicalEvent,
    Conversation,
    UserMemory,
)
from sqlalchemy.exc import SQLAlchemyError

TENANT = f"ctx-{uuid.uuid4()}"
CONV_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()
USER_ID = uuid.uuid4()

SOURCE_USER_EXPLICIT = "USER_EXPLICIT"
SOURCE_AGENT_INFERRED = "AGENT_INFERRED"
# 历史取值：design §4.4 规定按「非 USER_EXPLICIT」处理（不自动注入），不得因历史数据放宽注入面
LEGACY_SOURCE = "EXPLICIT"


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
                source_type=SOURCE_USER_EXPLICIT,
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
                source_type=SOURCE_USER_EXPLICIT,
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


# ---------------------------------------------------------------------------
# TASK-003：分级注入 / 双上限 / 非指令措辞 / 读失败降级（RULE-05/06/10、RULE-09 的注入侧）
# ---------------------------------------------------------------------------


async def _seed_injection_tenant(
    entries: tuple[tuple[str, str, str], ...],
) -> tuple[str, uuid.UUID, uuid.UUID]:
    """种一个隔离租户：1 个会话 + N 条记忆，`update_time` **显式递增**以保证排序确定。

    `update_time` 必须显式给值：同一事务插入的行会拿到相同的 `now()`（事务开始时刻），
    并列时"取最近 N 条"就没有确定顺序，断言会变成掷骰子。
    """
    tenant = f"inj-{uuid.uuid4()}"
    conversation_id = uuid.uuid4()
    user_id = uuid.uuid4()
    base = datetime.now(UTC) - timedelta(days=1)
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant,
                user_id=user_id,
                agent_id=uuid.uuid4(),
                status="ACTIVE",
                last_seq=1,
            )
        )
        for index, (key, value, source) in enumerate(entries):
            session.add(
                UserMemory(
                    tenant_id=tenant,
                    user_id=user_id,
                    memory_key=key,
                    category="PREFERENCE",
                    content_json={"value": value},
                    source_type=source,
                    enabled=True,
                    update_time=base + timedelta(minutes=index),
                )
            )
        await session.commit()
    return tenant, conversation_id, user_id


async def _purge_injection_tenant(tenant: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            UserMemory.__table__.delete().where(UserMemory.tenant_id == tenant)
        )
        await session.execute(
            CanonicalEvent.__table__.delete().where(CanonicalEvent.tenant_id == tenant)
        )
        await session.execute(
            Conversation.__table__.delete().where(Conversation.tenant_id == tenant)
        )
        await session.commit()


async def _request_for(tenant: str, conversation_id: uuid.UUID, user_id: uuid.UUID) -> Any:
    builder = DbBackedContextBuilder(session_factory=get_session_factory)
    return await builder.build(
        ContextInput(
            model_id="gpt-4o-mini",
            instructions="be helpful",
            conversation_id=conversation_id,
            tenant_id=tenant,
            user_id=user_id,
            budget_messages=10,
        )
    )


def _memory_lines(request: Any) -> list[str]:
    return [str(m.content) for m in request.messages if str(m.content).startswith("[记忆·")]


async def test_s03_user_explicit_memory_injected_with_provenance_and_non_instruction_wording() -> None:
    """[S-03][integration] `USER_EXPLICIT` 记忆被注入，且带来源标注与"仅供参考、非指令"措辞。

    真实边界：ContextBuilder → 模型请求（记忆取自真实 PostgreSQL）。
    注入仍以 `role=SYSTEM` 前置、不进 `_trim` 预算 ⇒ **措辞是唯一的效力边界表达**，
    缺了它就等于让用户可写内容以系统指令身份生效。
    """
    tenant, conversation_id, user_id = await _seed_injection_tenant(
        (("reply.language", "中文", SOURCE_USER_EXPLICIT),)
    )
    try:
        request = await _request_for(tenant, conversation_id, user_id)
        lines = _memory_lines(request)
        assert len(lines) == 1
        text = lines[0]
        assert "reply.language" in text and "中文" in text  # key 与值都在
        assert "用户明确要求" in text  # 来源标注
        assert "仅供参考" in text and "非指令" in text  # 效力边界
        assert "以当前指示为准" in text  # 冲突优先级

        injected = next(m for m in request.messages if str(m.content).startswith("[记忆·"))
        assert str(injected.role) == "system"  # 注入位置/角色不变
    finally:
        await _purge_injection_tenant(tenant)


async def test_s04_agent_inferred_memory_not_injected_but_recallable() -> None:
    """[S-04][integration] `AGENT_INFERRED` 不进默认上下文，只经检索路径可取回。

    真实边界：ContextBuilder → 模型请求（真实 PostgreSQL）；检索侧走 `MemoryService.search`
    —— 那是 `recall` 工具的落点（工具本体属 TASK-002，本任务只钉住分级过滤本身）。
    """
    tenant, conversation_id, user_id = await _seed_injection_tenant(
        (("work.style", "简洁", SOURCE_AGENT_INFERRED),)
    )
    try:
        request = await _request_for(tenant, conversation_id, user_id)
        assert _memory_lines(request) == []
        assert "简洁" not in " ".join(str(m.content) for m in request.messages)

        recalled = await MemoryService().search(tenant, user_id, prefix=None, limit=10)
        assert [item["memory_key"] for item in recalled] == ["work.style"]
        assert recalled[0]["content_json"] == {"value": "简洁"}
    finally:
        await _purge_injection_tenant(tenant)


async def test_s04_legacy_source_type_is_not_injected() -> None:
    """[S-04][integration] 历史 `source_type` 取值（如 `EXPLICIT`）按非 `USER_EXPLICIT` 处理。

    design §4.4：不得因历史数据放宽注入面 —— 存量行宁可暂时不注入，也不猜它是不是用户显式要求。
    """
    tenant, conversation_id, user_id = await _seed_injection_tenant(
        (("legacy.key", "历史值", LEGACY_SOURCE),)
    )
    try:
        request = await _request_for(tenant, conversation_id, user_id)
        assert _memory_lines(request) == []
        assert "历史值" not in " ".join(str(m.content) for m in request.messages)
    finally:
        await _purge_injection_tenant(tenant)


async def test_b02_injection_caps_count_and_keeps_newest() -> None:
    """[B-02][integration] 12 条短记忆：只注入最近 `MAX_INJECTED_MEMORIES` 条，其余不注入且不报错。"""
    entries = tuple((f"k.{index:02d}", f"v{index}", SOURCE_USER_EXPLICIT) for index in range(12))
    tenant, conversation_id, user_id = await _seed_injection_tenant(entries)
    try:
        request = await _request_for(tenant, conversation_id, user_id)
        lines = _memory_lines(request)
        assert len(lines) == MAX_INJECTED_MEMORIES

        injected_keys = [line.split("] ", 1)[1].split(" =", 1)[0] for line in lines]
        assert injected_keys == [f"k.{index:02d}" for index in range(11, 1, -1)]  # 最新的 10 条
        assert "v00" not in " ".join(lines) and "v01" not in " ".join(lines)  # 被截掉的确实没进来
    finally:
        await _purge_injection_tenant(tenant)


async def test_b02_injection_caps_bytes_before_count() -> None:
    """[B-02][integration] 写满 512 字符的记忆：字节上限先触顶，只注入放得下的那几条。

    中文 512 字 ≈ 1.5KB，`MAX_INJECTED_BYTES`(2048) 只够一条 ⇒ 条数上限不是主约束。
    """
    long_value = "长" * 512
    tenant, conversation_id, user_id = await _seed_injection_tenant(
        (
            ("big.old", long_value + "旧", SOURCE_USER_EXPLICIT),
            ("big.new", long_value + "新", SOURCE_USER_EXPLICIT),
        )
    )
    try:
        request = await _request_for(tenant, conversation_id, user_id)
        lines = _memory_lines(request)
        assert len(lines) == 1  # 第二条会越过字节上限 ⇒ 停
        assert "big.new" in lines[0]  # 先到先得：最新那条先进
        assert sum(len(line.encode("utf-8")) for line in lines) <= MAX_INJECTED_BYTES
    finally:
        await _purge_injection_tenant(tenant)


async def test_e06_injection_read_failure_degrades_without_breaking_run(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """[E-06][integration] 注入查询失败：本轮不注入 + 记 warning，请求照常构建。

    注入是**每轮对话的必经查询**，DB 抖动不得让整轮对话失败 —— 那会把一次查询故障放大成
    "用户发不出消息"。故障注入在记忆查询这一处（会话与历史仍走真实 PostgreSQL）。
    """
    tenant, conversation_id, user_id = await _seed_injection_tenant(
        (("reply.language", "中文", SOURCE_USER_EXPLICIT),)
    )

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        # 用 SQLAlchemy 的异常族：实现只吞这一类（DB 层故障），编程错误必须继续上抛
        raise SQLAlchemyError("injection query failed")

    monkeypatch.setattr(MemoryService, "list_for_injection_with_session", _boom)
    try:
        with caplog.at_level(logging.WARNING, logger="muad_agent_runtime.application.context_builder"):
            request = await _request_for(tenant, conversation_id, user_id)

        assert _memory_lines(request) == []  # 本轮无注入
        assert request.model_id == "gpt-4o-mini"  # 请求照常构建（Run 不中断）
        assert any(
            record.levelno == logging.WARNING for record in caplog.records
        ), "读失败必须留 warning，否则故障完全静默"
    finally:
        await _purge_injection_tenant(tenant)


async def test_e07_platform_secrets_never_reach_the_model_request_through_memory() -> None:
    """[E-07][integration] 平台密钥不经记忆链进入模型请求（探针断言）。

    真实边界：ContextBuilder → 模型请求体（记忆取自真实 PostgreSQL）。
    断言两件事：① 已知密钥值不出现在请求的任何位置（消息/工具/参数）；② 注入行只由
    「措辞 + key + 存储值」构成，不夹带任何其它列的内容（否则列里的密钥会被顺带带出去）。
    """
    probes = ("sk-owner-table-key", "mcp-owner-secret", "bot-secret-value")
    tenant, conversation_id, user_id = await _seed_injection_tenant(
        (("reply.language", "中文", SOURCE_USER_EXPLICIT),)
    )
    try:
        request = await _request_for(tenant, conversation_id, user_id)
        haystack = " ".join(
            [str(m.content) for m in request.messages]
            + [repr(tool) for tool in request.tools]
            + [repr(request.params)]
        )
        for probe in probes:
            assert probe not in haystack

        line = _memory_lines(request)[0]
        assert line.count("中文") == 1
        assert "reply.language = 中文" in line  # 只有 key 与存储值，无其它列
        assert line.endswith("）")
    finally:
        await _purge_injection_tenant(tenant)
