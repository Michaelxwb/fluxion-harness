"""[S-08/S-03/S-04/B-02/E-06/E-07] 上下文重建与记忆注入（0020 新契约）。

2026-10-15 随 TASK-031 改写：删除已不存在的 `source_type`/旧前缀/比例断言，改为稳定身份 +
`author/version` 契约。记忆注入不再是 `load_history` 的一部分，而是每个模型请求前由
`MemoryContextLoader` 刷新的具名 `MemoryComponent`（见 docs/04 §10.2/§11.1）。

不得 Mock 的真实边界：真实 PostgreSQL（每用例独立 head 库）+ 真实 `DbBackedContextBuilder` /
`MemoryCandidateCache` / `MemoryContextLoader` + 真实模型 HTTP 探针（`request_round`）。
"""

from __future__ import annotations

import logging
import re
import uuid
from itertools import takewhile
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_core.context.compactor import trim_history
from muad_agent_core.memory.render import MEMORY_NOTICE
from muad_agent_core.model.provider import ModelMessage, ModelRole
from muad_agent_runtime.application.context_builder import DbBackedContextBuilder
from muad_agent_runtime.infrastructure.models.runtime import (
    Artifact,
    CanonicalEvent,
    Conversation,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.agent_runtime.memory_context_support import loader, memory_text, request_round
from tests.agent_runtime.memory_redesign_write_support import context, seed_facts
from tests.agent_runtime.memory_tool_support import recall, tools
from tests.agent_runtime.test_memory_redesign_cache import queries

pytestmark = pytest.mark.integration

RUN_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def _factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def _count_events(engine, tenant: str) -> int:
    async with engine.connect() as connection:
        return int(
            (
                await connection.execute(
                    sa.text("SELECT count(*) FROM runtime.canonical_event WHERE tenant_id = :tenant"),
                    {"tenant": tenant},
                )
            ).scalar_one()
        )


async def _seed_history(engine, *, tenant: str, conversation_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """一个会话 + 一轮真实工具回合 + 一份大 artifact（历史装配/裁剪用例共用）。"""
    artifact_id = uuid.uuid4()
    async with _factory(engine).begin() as session:
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant,
                user_id=user_id,
                agent_id=uuid.uuid4(),
                status="ACTIVE",
                last_seq=6,
            )
        )
        session.add_all(
            [
                CanonicalEvent(
                    tenant_id=tenant,
                    conversation_id=conversation_id,
                    run_id=RUN_ID,
                    seq=1,
                    event_type="USER_MESSAGE",
                    payload_json={"text": "hello"},
                ),
                # assistant 回合：声明了 c1（含思维链）。缺了它，下面的 TOOL_CALL 就是
                # 「孤儿 tool 消息」，历史重建会整条跳过（供应商会拒绝这种历史）。
                CanonicalEvent(
                    tenant_id=tenant,
                    conversation_id=conversation_id,
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
                    tenant_id=tenant,
                    conversation_id=conversation_id,
                    run_id=RUN_ID,
                    seq=3,
                    event_type="TOOL_CALL",
                    payload_json={"tool_name": "search", "tool_call_id": "c1"},
                    stream_type="tool.started",
                    artifact_id=artifact_id,
                ),
                CanonicalEvent(
                    tenant_id=tenant,
                    conversation_id=conversation_id,
                    run_id=RUN_ID,
                    seq=4,
                    event_type="ASSISTANT_MESSAGE",
                    payload_json={"text": "found it"},
                ),
                # 其他租户的事件：不得泄漏
                CanonicalEvent(
                    tenant_id=f"{tenant}-other",
                    conversation_id=uuid.uuid4(),
                    run_id=uuid.uuid4(),
                    seq=1,
                    event_type="USER_MESSAGE",
                    payload_json={"text": "leak?"},
                ),
                Artifact(
                    id=artifact_id,
                    tenant_id=tenant,
                    run_id=RUN_ID,
                    conversation_id=conversation_id,
                    artifact_type="TOOL_RESULT",
                    storage_key="tools/x/result.bin",
                    media_type="text/plain",
                    size=5000,
                    checksum="sha256:" + "0" * 64,
                    preview_text="PREVIEW-CHUNK",
                ),
            ]
        )


def _builder(engine) -> DbBackedContextBuilder:
    return DbBackedContextBuilder(session_factory=lambda: _factory(engine))


async def test_s08_context_from_db_with_isolation_and_preview(async_tool_database) -> None:
    """[S-08] 从 DB 构建请求：隔离/预览/裁剪不修改事件（append-only）。"""
    tenant, conversation_id = f"ctx-{uuid.uuid4()}", uuid.uuid4()
    await _seed_history(
        async_tool_database, tenant=tenant, conversation_id=conversation_id, user_id=USER_ID
    )
    before = await _count_events(async_tool_database, tenant)

    # 走**生产路径**（`load_history`）：记忆已改为请求边界组件（S-03 起覆盖），不在这里。
    messages = await _builder(async_tool_database).load_history(
        tenant_id=tenant,
        conversation_id=conversation_id,
        user_id=USER_ID,
        budget_messages=10,
    )

    content = " ".join(str(m.content) for m in messages)
    assert "hello" in content
    assert "found it" in content
    assert "leak?" not in content  # 跨租户隔离

    # Artifact：大结果仅 preview
    assert "PREVIEW-CHUNK" in content
    assert "x" * 200_000 not in content

    # assistant 回合连 tool_calls 与思维链一起进历史：思考模式要求带 tool_calls 的消息
    # 必须回传 reasoning_content，否则整条请求被供应商拒绝
    turn = next(m for m in messages if m.tool_calls)
    assert [call.id for call in turn.tool_calls] == ["c1"]
    assert turn.reasoning_content == "REASONING-CHUNK"
    assert [m.tool_call_id for m in messages if m.tool_call_id is not None] == ["c1"]

    assert await _count_events(async_tool_database, tenant) == before  # append-only


async def test_s08_budget_trims_only_request_keeps_tool_pairs(async_tool_database) -> None:
    """[S-08] 条数预算只作用于**派生请求**：受保护前缀保住、成对性保住、库不动。"""
    tenant, conversation_id = f"ctx-{uuid.uuid4()}", uuid.uuid4()
    await _seed_history(
        async_tool_database, tenant=tenant, conversation_id=conversation_id, user_id=USER_ID
    )
    ctx = context(tenant=tenant, user=USER_ID)
    await seed_facts(async_tool_database, ctx, 1, author="USER")
    memory = loader(async_tool_database, ctx)
    component = await memory.refresh()
    assert component is not None and component.message is not None, "样本前提：存在真实记忆组件"

    messages = await _builder(async_tool_database).load_history(
        tenant_id=tenant,
        conversation_id=conversation_id,
        user_id=USER_ID,
        budget_messages=2,
    )
    # Runner 的请求形态：system 提示 + memory 组件 + 历史（组件插入在首条之后）。
    request_messages = (ModelMessage(ModelRole.SYSTEM, "fixed instructions"), component.message, *messages)
    out = trim_history(request_messages, 2)

    protected_roles = [
        str(message.role)
        for message in takewhile(lambda item: item.role is ModelRole.SYSTEM, request_messages)
    ]
    assert protected_roles == ["system", "system"]  # 提示 + 记忆组件都在受保护前缀
    assert [str(message.role) for message in out[: len(protected_roles)]] == protected_roles

    # 成对性：留下来的 tool 消息必须有声明它的 assistant 回合（切点落在组中间时整条丢弃）。
    declared = {call.id for message in out for call in message.tool_calls}
    orphans = [m for m in out if m.role is ModelRole.TOOL and m.tool_call_id not in declared]
    assert not orphans, f"裁剪留下了孤儿 TOOL：{orphans}"

    # 裁剪不修改 DB（种入 4 条本租户事件）
    assert await _count_events(async_tool_database, tenant) == 4


async def test_orphan_tool_messages_are_dropped_from_history(async_tool_database) -> None:
    """没有前置 assistant 回合的 tool 消息**不得**进历史。

    回归（2026-10-01 排查）：`ASSISTANT_TURN` 上线前的存量会话里，tool 消息前面没有
    assistant `tool_calls`，供应商会拒绝**整个**请求 —— 实测原话：`Messages with role
    'tool' must be a response to a preceding message with 'tool_calls'`。用户侧表现为
    「会话用过一次工具之后就永久不可用，只能 /new 逃生」。历史重建改为「成对才收」后，
    孤儿一律跳过 ⇒ 存量会话无需迁移即可自愈（代价是丢那几轮的工具上下文）。
    """
    tenant, conv_id = f"orphan-{uuid.uuid4()}", uuid.uuid4()
    async with _factory(async_tool_database).begin() as session:
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

    messages = await _builder(async_tool_database).load_history(
        tenant_id=tenant,
        conversation_id=conv_id,
        user_id=USER_ID,
        budget_messages=20,
    )
    roles = [str(message.role) for message in messages]
    # 系统提示不在这里（由 `AgentRunner` 的 `DefaultPromptBuilder` 负责）
    assert roles == ["user", "assistant"], roles
    assert all(message.tool_call_id is None for message in messages)
    assert all(not message.tool_calls for message in messages)


async def test_unanswered_tool_calls_are_dropped_from_history(async_tool_database) -> None:
    """[RULE-01] assistant 声明了、却**没有结果落库**的调用不得回放。

    中断/取消打断一个多工具回合、或工具预算耗尽时，`ASSISTANT_TURN` 已经落库而某个结果没有
    （canonical 行只为真正跑过的调用写）。整份回放就会发出「声明 2 个、只答 1 个」的请求 ——
    与「孤儿 tool 消息」是同一类形状问题，供应商同样会拒**整个**请求（2026-10-06 review）。
    """
    tenant, conv_id = f"unanswered-{uuid.uuid4()}", uuid.uuid4()
    async with _factory(async_tool_database).begin() as session:
        session.add(
            Conversation(
                id=conv_id,
                tenant_id=tenant,
                user_id=USER_ID,
                agent_id=uuid.uuid4(),
                status="ACTIVE",
                last_seq=5,
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
                    payload_json={"text": "看图并搜索"},
                ),
                # 声明了两个调用，只有 `a` 有结果（`b` 被中断吞掉）
                CanonicalEvent(
                    tenant_id=tenant,
                    conversation_id=conv_id,
                    run_id=RUN_ID,
                    seq=2,
                    event_type="ASSISTANT_TURN",
                    payload_json={
                        "text": "我先看看",
                        "tool_calls": [
                            {"id": "a", "name": "view_image", "arguments": {}},
                            {"id": "b", "name": "search_attachment", "arguments": {}},
                        ],
                    },
                ),
                CanonicalEvent(
                    tenant_id=tenant,
                    conversation_id=conv_id,
                    run_id=RUN_ID,
                    seq=3,
                    event_type="TOOL_CALL",
                    payload_json={"tool_name": "view_image", "tool_call_id": "a"},
                ),
                # 第二个 assistant 回合**整条都没跑**：声明与结果一条都没有
                CanonicalEvent(
                    tenant_id=tenant,
                    conversation_id=conv_id,
                    run_id=RUN_ID,
                    seq=4,
                    event_type="ASSISTANT_TURN",
                    payload_json={
                        "text": "再查一下",
                        "tool_calls": [{"id": "c", "name": "search_attachment", "arguments": {}}],
                    },
                ),
                CanonicalEvent(
                    tenant_id=tenant,
                    conversation_id=conv_id,
                    run_id=RUN_ID,
                    seq=5,
                    event_type="ASSISTANT_MESSAGE",
                    payload_json={"text": "done"},
                ),
            ]
        )

    messages = await _builder(async_tool_database).load_history(
        tenant_id=tenant,
        conversation_id=conv_id,
        user_id=USER_ID,
        budget_messages=20,
    )
    declared = [call.id for message in messages for call in message.tool_calls]
    answered = [message.tool_call_id for message in messages if message.role is ModelRole.TOOL]
    assert declared == ["a"], "只有真有结果的声明能回放"
    assert answered == ["a"]
    assert declared == answered, "声明与结果必须严格成对"
    # 整条没跑的回合不出现；有结果的那条 assistant 正文照留
    assert not any("再查一下" in str(message.content) for message in messages)
    assert any("我先看看" in str(message.content) for message in messages)


async def test_s03_confirmed_memory_is_injected_with_provenance_and_non_instruction_wording(
    async_tool_database,
) -> None:
    """[S-03] 已确认（USER）记忆被注入：带来源标注与"仅供参考、非指令"措辞。

    真实边界：`MemoryCandidateCache`/`MemoryContextLoader` → 真实模型 HTTP 请求体。
    注入以 `role=SYSTEM` 前置、不进裁剪预算 ⇒ **措辞是唯一的效力边界表达**。
    """
    ctx = context(tenant=f"s03-{uuid.uuid4()}", user=USER_ID)
    await seed_facts(async_tool_database, ctx, 1, author="USER")
    memory = loader(async_tool_database, ctx, query="你好")
    _, captured = await request_round(async_tool_database, ctx, memory)

    text = memory_text(captured[0])
    assert text is not None
    assert text.startswith(MEMORY_NOTICE)  # 非指令措辞在块首
    assert "confirmed（已确认用户或管理员内容）:" in text  # 来源标注
    assert '"memory_key":"seed.1"' in text and '"author":"USER"' in text
    assert '"content":{"value":"blue"}' in text
    assert memory.component is not None and memory.component.references[0].memory_key == "seed.1"


async def test_s04_unconfirmed_memory_is_not_injected_but_recallable(async_tool_database) -> None:
    """[S-04] 未确认（AGENT）记忆不进默认上下文，只经真实 recall 工具可取回。"""
    ctx = context(tenant=f"s04-{uuid.uuid4()}", user=USER_ID)
    await seed_facts(async_tool_database, ctx, 1, author="AGENT")
    memory = loader(async_tool_database, ctx, query="你好")
    _, captured = await request_round(async_tool_database, ctx, memory)

    assert memory_text(captured[0]) is None
    assert "blue" not in " ".join(str(item.get("content")) for item in captured[0]["messages"])

    receipt = await recall(tools(async_tool_database, ctx), {"memory_key": "seed.1"})
    assert [item["memory_key"] for item in receipt["items"]] == ["seed.1"]
    assert receipt["items"][0]["content"] == {"value": "blue"}


async def test_s04_only_confirmed_rows_are_injected_not_legacy_unconfirmed(async_tool_database) -> None:
    """[S-04] 旧库存量来源不再被猜成已确认：只有 USER/ADMIN 行进默认注入面。

    0020 迁移把无可证明确认的旧来源统一落为 AGENT（见 `tests/migrations/test_memory_redesign_migration.py`）；
    本用例在注入边界钉住「确认级别决定注入，存在性不决定」。
    """
    ctx = context(tenant=f"s04b-{uuid.uuid4()}", user=USER_ID)
    await seed_facts(async_tool_database, ctx, 1, author="USER", prefix="confirmed")
    await seed_facts(async_tool_database, ctx, 1, author="AGENT", prefix="legacy")
    memory = loader(async_tool_database, ctx, query="你好")
    _, captured = await request_round(async_tool_database, ctx, memory)

    text = memory_text(captured[0])
    assert '"memory_key":"confirmed.1"' in text
    assert "legacy.1" not in text


async def test_b02_injection_caps_count_and_keeps_newest(async_tool_database) -> None:
    """[B-02] 12 条已确认记忆：只注入最近 `max_injected_memories` 条，其余计丢弃且不报错。"""
    ctx = context(tenant=f"b02-{uuid.uuid4()}", user=USER_ID)
    await seed_facts(async_tool_database, ctx, 12, author="USER")
    async with async_tool_database.begin() as connection:
        await connection.execute(
            sa.text(
                "UPDATE runtime.user_memory"
                " SET update_time = statement_timestamp()"
                " - ((13 - substring(memory_key from 6)::int) * interval '1 minute')"
                " WHERE tenant_id = :tenant"
            ),
            {"tenant": ctx.tenant_id},
        )
    memory = loader(async_tool_database, ctx, query=None)
    _, captured = await request_round(async_tool_database, ctx, memory)

    text = memory_text(captured[0])
    keys = re.findall(r'"memory_key":"(seed\.\d+)"', text)
    assert keys == [f"seed.{index}" for index in range(12, 2, -1)]  # 最新的 10 条
    assert "seed.1" not in keys and "seed.2" not in keys  # 被截掉的确实没进来
    assert memory.component is not None and memory.component.block.dropped_count == 2


async def test_b02_injection_caps_bytes_before_count(async_tool_database) -> None:
    """[B-02] 写满 512 字符的记忆：字节上限先触顶，只注入放得下的那条（无首条越限）。"""
    ctx = context(tenant=f"b02b-{uuid.uuid4()}", user=USER_ID)
    async with async_tool_database.begin() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO runtime.user_memory (tenant_id,user_id,memory_key,category,content_json,"
                "author,explicit_request,confirmation_event_id,write_policy,update_time) VALUES"
                " (:tenant,:user,'big.old','PREFERENCE',"
                " jsonb_build_object('value',CAST(:old AS text)),'USER',true,"
                " gen_random_uuid(),'SERVER_CONTROLLED',statement_timestamp() - interval '1 minute'),"
                " (:tenant,:user,'big.new','PREFERENCE',"
                " jsonb_build_object('value',CAST(:new AS text)),'USER',true,"
                " gen_random_uuid(),'SERVER_CONTROLLED',statement_timestamp())"
            ),
            {
                "tenant": ctx.tenant_id,
                "user": ctx.user_id,
                "old": "长" * 511 + "旧",
                "new": "长" * 511 + "新",
            },
        )
    memory = loader(async_tool_database, ctx, query=None)
    _, captured = await request_round(async_tool_database, ctx, memory)

    text = memory_text(captured[0])
    assert '"memory_key":"big.new"' in text  # 最新那条先进
    assert "big.old" not in text  # 第二条越过字节上限 ⇒ 跳过
    assert len(text.encode("utf-8")) <= 2048
    assert memory.component is not None and memory.component.block.dropped_bytes == 1


async def test_e06_injection_read_failure_degrades_without_breaking_run(
    async_tool_database, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """[E-06] 注入查询失败：本轮不注入 + 记 warning，请求照常构建（Run 不中断）。"""
    ctx = context(tenant=f"e06-{uuid.uuid4()}", user=USER_ID)
    await seed_facts(async_tool_database, ctx, 1, author="USER")

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        # 用 SQLAlchemy 的异常族：实现只吞这一类（DB 层故障），编程错误必须继续上抛
        raise SQLAlchemyError("injection query failed")

    monkeypatch.setattr(type(queries(async_tool_database)), "read_snapshot", _boom)
    memory = loader(async_tool_database, ctx)
    with caplog.at_level(logging.WARNING, logger="muad_agent_runtime.application.memory_cache"):
        _, captured = await request_round(async_tool_database, ctx, memory)

    assert memory_text(captured[0]) is None  # 本轮无注入
    assert memory.component is None  # 失败后不保留旧组件
    assert any(record.levelno == logging.WARNING for record in caplog.records), (
        "读失败必须留 warning，否则故障完全静默"
    )


async def test_e07_platform_secrets_never_reach_the_model_request_through_memory(
    async_tool_database,
) -> None:
    """[E-07] 平台密钥不经记忆链进入模型请求（探针断言）。

    断言两件事：① 已知密钥值不出现在请求的任何位置（消息/工具/参数）；② 注入行只由
    「措辞 + key + 存储值 + 受控元数据」构成，不夹带其它列（如 `source_quote`）。
    """
    probes = ("sk-owner-table-key", "mcp-owner-secret", "bot-secret-value")
    ctx = context(tenant=f"e07-{uuid.uuid4()}", user=USER_ID)
    await seed_facts(async_tool_database, ctx, 1, author="USER")
    # 非渲染列里埋入 canary：渲染必须只取受控字段，不能整行外带
    async with async_tool_database.begin() as connection:
        await connection.execute(
            sa.text(
                "UPDATE runtime.user_memory SET source_quote = :probe WHERE tenant_id = :tenant"
            ),
            {"probe": probes[0], "tenant": ctx.tenant_id},
        )
    memory = loader(async_tool_database, ctx, query="你好")
    _, captured = await request_round(async_tool_database, ctx, memory)

    body = captured[0]
    haystack = " ".join(
        [str(message.get("content")) for message in body["messages"]]
        + [repr(body.get("tools"))]
        + [repr(body.get("params"))]
    )
    for probe in probes:
        assert probe not in haystack

    text = memory_text(body)
    assert text.count("blue") == 1
    assert '"content":{"value":"blue"}' in text
    assert "source_quote" not in text  # 只有受控字段，不整行外带
