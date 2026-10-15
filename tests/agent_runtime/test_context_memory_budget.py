"""[E-06] memory 预算与 micro 豁免（0020 新契约）。

2026-10-15 随 TASK-031 改写：`memory.budget_ratio` 已从 schema 删除，注入只有**硬上限**
（条数 × 完整 UTF-8 字节，Run 创建时冻结）；旧「比例 × 历史字节」断言替换为「历史长度不改变
注入预算、超限条目跳过、注入段落逐字且不受 micro 改写」。

不得 Mock 的真实边界：真实 agent-runtime 请求装配（真实 PG 的 `canonical_event` + `user_memory`
→ `MemoryCandidateCache`/`MemoryContextLoader` → `AgentRunner`）→ **真实模型 HTTP 探针**
（`tests/e2e/openai_probe_app.py`：`GET /requests` 回放模型实际收到的请求体）。
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI
from muad_agent_core.agent import AgentRunner
from muad_agent_core.hooks import HookPipeline
from muad_agent_core.memory.render import MEMORY_NOTICE
from muad_agent_core.model import ModelProvider, OpenAICompatibleProvider
from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry
from muad_agent_runtime.api.deps import (
    get_credentials_client,
    get_executor_factory,
    get_resolve_client,
)
from muad_agent_runtime.application.attachments.tool_results import ArtifactResultWriter
from muad_agent_runtime.application.context_compaction import RuntimeContextCompactor
from muad_agent_runtime.application.executor import (
    AgentRunnerExecutor,
    ExecutorFactory,
    ExecutorRequest,
    RunExecutor,
    ToolCallRecorder,
    execution_defaults_for,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
)
from muad_agent_runtime.main import app
from muad_common import SharedSettings
from muad_contracts.platform_settings import (
    CompactionSettings,
    MicroSettings,
    SnipSettings,
    ToolResultSettings,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from agent_runtime.conftest import FakeResolveClient, TenantContext, parse_sse
from tests.agent_runtime.memory_context_support import loader, memory_text, request_round
from tests.agent_runtime.memory_redesign_write_support import (
    command,
    context,
    upsert,
    writer,
)
from tests.e2e.openai_probe_app import app as probe_app

LISTEN_TIMEOUT_SEC = 15.0
TOOL_NAME = "dump"
RESULT_TEXT = "很长的工具结果" * 900  # ≈ 9 KiB ⇒ 单条超外置阈值，会被换成引用
#: 历史要够长：旧比例口径下 15000 字节 × 0.001 = 15 字节（一条都放不下）；新契约不受它影响。
LONG_HISTORY_TEXT = "历史" * 2500
MEMORIES = {
    "reply.language": "以后都用中文回答我",
    "reply.style": "回答尽量简短",
    "reply.signoff": "结尾不要加签名档",
}


def _tool_texts(body: Mapping[str, Any]) -> list[str]:
    return [
        str(message.get("content"))
        for message in body.get("messages") or []
        if isinstance(message, dict) and message.get("role") == "tool"
    ]


def _injected_block(body: Mapping[str, Any]) -> str:
    """模型实际收到的记忆块（按新契约：块首为非指令 notice）。"""
    blocks = [
        str(message.get("content"))
        for message in body.get("messages") or []
        if isinstance(message, dict)
        and message.get("role") == "system"
        and MEMORY_NOTICE in str(message.get("content"))
    ]
    assert len(blocks) <= 1, f"记忆组件必须唯一，实得 {len(blocks)}"
    return blocks[0] if blocks else ""


async def _seed(tenant: TenantContext) -> None:
    """种下：一个带长历史的会话 + 三条已确认（ADMIN 写入）记忆。"""
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.begin() as connection:
            conversation_id = uuid.uuid4()
            await connection.execute(
                sa.text(
                    "INSERT INTO runtime.conversation (id,tenant_id,user_id,agent_id,status,last_seq)"
                    " VALUES (:id,:tenant,:user,:agent,'ACTIVE',1)"
                ),
                {
                    "id": conversation_id,
                    "tenant": tenant.tenant_id,
                    "user": tenant.platform_user_id,
                    "agent": tenant.agent_id,
                },
            )
            await connection.execute(
                sa.text(
                    "INSERT INTO runtime.canonical_event (tenant_id,conversation_id,seq,event_type,"
                    "payload_json) VALUES (:tenant,:conversation,1,'USER_MESSAGE',"
                    " jsonb_build_object('text', CAST(:text AS text)))"
                ),
                {"tenant": tenant.tenant_id, "conversation": conversation_id, "text": LONG_HISTORY_TEXT},
            )
        ctx = context(tenant=tenant.tenant_id, user=tenant.platform_user_id)
        await writer(engine).execute(
            ctx,
            command(*(upsert(key, value) for key, value in MEMORIES.items())),
        )
    finally:
        await engine.dispose()


def _registry() -> ToolRegistry:
    registry = ToolRegistry()

    async def handler(arguments: Mapping[str, Any], *, call_id: str) -> str:
        return RESULT_TEXT

    registry.register(
        ToolDefinition(
            name=TOOL_NAME,
            description="dump",
            input_schema={"type": "object", "properties": {}},
            effect=ToolEffect.READ,
            handler=handler,
        )
    )
    return registry


@asynccontextmanager
async def _serve_http(target: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    config = uvicorn.Config(target, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        deadline = time.monotonic() + LISTEN_TIMEOUT_SEC
        while not server.started and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert server.started, "未在超时内监听"
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30.0) as client:
            yield client
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=LISTEN_TIMEOUT_SEC)


def _memory_loader(request: ExecutorRequest):
    """与生产 `_memory_runtime_for` 同口径：请求边界刷新的真实 loader（冻结 memory 策略）。"""
    from muad_agent_runtime.application.memory_cache import MemoryCandidateCache
    from muad_agent_runtime.application.memory_context import MemoryContextLoader
    from muad_agent_runtime.application.memory_queries import MemoryQueries, MemoryReadScope

    run_context = request.run_context
    assert run_context is not None
    queries = MemoryQueries(get_session_factory())
    cache = MemoryCandidateCache(
        queries,
        scope=MemoryReadScope(tenant_id=run_context.tenant_id, user_id=run_context.user_id),
        run_id=run_context.run_id,
    )
    return MemoryContextLoader(
        cache,
        settings=execution_defaults_for(request).memory_policy,
        query=request.input_text,
    )


def _factory(probe_url: str, budget: dict[str, Any], tmp_path: Path) -> ExecutorFactory:
    """生产同款装配：工具结果经 recorder 外置（不然 micro 无物可降级）+ 请求缝真实压缩器。"""

    async def factory(request: ExecutorRequest) -> RunExecutor:
        context_ = request.run_context
        assert context_ is not None
        provider: ModelProvider = OpenAICompatibleProvider(
            base_url=f"{probe_url}/v1", model="probe-model", api_key="probe-key"
        )
        settings = CompactionSettings(
            snip=SnipSettings(max_groups=99),  # 本用例只关心 memory 与 micro
            micro=MicroSettings(
                enabled=bool(budget["micro"]), keep_recent_tool_groups=int(budget["keep_recent"])
            ),
        )
        recorder = ToolCallRecorder(
            context=context_,
            audit_writer=None,
            artifact_writer=ArtifactResultWriter(tmp_path),
            settings=ToolResultSettings(),
        )
        runner = AgentRunner(
            provider=provider,
            registry=_wrap_registry(_registry(), recorder),
            hooks=HookPipeline(),
            tool_round_results=recorder,
            context_loader=_memory_loader(request),
            context_compactor=RuntimeContextCompactor(
                settings=settings,
                tenant_id=context_.tenant_id,
                run_id=context_.run_id,
                conversation_id=context_.conversation_id,
                artifact_root=tmp_path,
            ),
        )
        return AgentRunnerExecutor(runner=runner, request=replace(request, compaction=settings))

    return factory


def _wrap_registry(registry: ToolRegistry, recorder: ToolCallRecorder) -> ToolRegistry:
    """与 `executor._wrap_registry` 同口径：每个工具处理函数都过 recorder。"""
    wrapped = ToolRegistry()
    for definition in registry.list():
        handler = definition.handler
        assert handler is not None
        wrapped.register(replace(definition, handler=partial(recorder, definition, handler=handler)))
    return wrapped


@asynccontextmanager
async def _stack(
    fake_resolve: FakeResolveClient, budget: dict[str, Any], tmp_path: Path
) -> AsyncIterator[tuple[httpx.AsyncClient, httpx.AsyncClient]]:
    """真实 Runtime（依赖覆盖与 `conftest.client` 同口径）+ 真实模型探针。"""
    async with _serve_http(probe_app) as probe:
        app.dependency_overrides[get_resolve_client] = lambda: fake_resolve
        app.dependency_overrides[get_executor_factory] = lambda: _factory(
            str(probe.base_url), budget, tmp_path
        )
        app.dependency_overrides[get_credentials_client] = lambda: None
        try:
            async with _serve_http(app) as client:
                yield client, probe
        finally:
            app.dependency_overrides.pop(get_resolve_client, None)
            app.dependency_overrides.pop(get_executor_factory, None)
            app.dependency_overrides.pop(get_credentials_client, None)
            # **必须用异步客户端**：探针与 Runtime 跑在同一个事件循环里，同步 httpx 会把循环堵死。
            async with httpx.AsyncClient(timeout=10.0) as cleanup:
                await cleanup.post(f"{probe.base_url}/script", json={})


async def _send(client: httpx.AsyncClient, tenant: TenantContext, text: str) -> None:
    response = await client.post(
        "/v1/runs",
        json={
            "agent_id": str(tenant.agent_id),
            "platform_user_id": str(tenant.platform_user_id),
            "channel": {"type": "WECOM", "bot_id": "bot-memory"},
            "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": text},
        },
        headers={"X-Tenant-Id": tenant.tenant_id},
    )
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    assert events[-1]["type"] == "run.completed", json.dumps(events[-1], ensure_ascii=False)


async def _last_request(probe: httpx.AsyncClient) -> Mapping[str, Any]:
    response = await probe.get("/requests", timeout=10.0)
    bodies = response.json().get("requests") or []
    assert bodies, "模型探针没收到任何请求"
    return bodies[-1]


async def _script_tool_round(probe: httpx.AsyncClient) -> None:
    """让探针先回一次工具调用，再收尾（同样走异步客户端，理由同上）。"""
    response = await probe.post(
        f"{probe.base_url}/script",
        json={
            "tools": [{"name": TOOL_NAME, "arguments": "{}"}],
            "final_text": "第一轮：我去取一下",
        },
        timeout=10.0,
    )
    response.raise_for_status()


async def test_e06_injection_is_hard_capped_without_history_ratio(async_tool_database) -> None:
    """注入只受硬上限约束：短历史也全量注入，历史变长不改变注入（无 ratio 分支）。"""
    ctx = context(tenant=f"e06-{uuid.uuid4()}", user=uuid.uuid4())
    await writer(async_tool_database).execute(
        ctx, command(*(upsert(key, value) for key, value in MEMORIES.items()))
    )
    short_history = loader(async_tool_database, ctx, query=None)
    _, captured_short = await request_round(async_tool_database, ctx, short_history)

    # 追加一条超长历史（旧口径下会把预算压到 15 字节）：注入必须逐字不变
    async with async_tool_database.begin() as connection:
        conversation_id = uuid.uuid4()
        await connection.execute(
            sa.text(
                "INSERT INTO runtime.conversation (id,tenant_id,user_id,agent_id,status,last_seq)"
                " VALUES (:id,:tenant,:user,:agent,'ACTIVE',1)"
            ),
            {
                "id": conversation_id,
                "tenant": ctx.tenant_id,
                "user": ctx.user_id,
                "agent": uuid.uuid4(),
            },
        )
        await connection.execute(
            sa.text(
                "INSERT INTO runtime.canonical_event (tenant_id,conversation_id,seq,event_type,"
                "payload_json) VALUES (:tenant,:conversation,1,'USER_MESSAGE',"
                " jsonb_build_object('text', CAST(:text AS text)))"
            ),
            {"tenant": ctx.tenant_id, "conversation": conversation_id, "text": LONG_HISTORY_TEXT},
        )
    long_history = loader(async_tool_database, ctx, query=None)
    _, captured_long = await request_round(
        async_tool_database,
        replace(ctx, conversation_id=conversation_id),
        long_history,
    )

    short_block = memory_text(captured_short[0])
    long_block = memory_text(captured_long[0])
    assert short_block is not None and short_block == long_block
    for key, value in MEMORIES.items():
        assert f'"memory_key":"{key}"' in short_block
        assert f'"value":"{value}"' in short_block
    assert len(short_block.encode("utf-8")) <= 2048, "硬上限 2048 字节是绝对兜底"


async def test_e06_injected_memory_is_verbatim_while_micro_degrades_tools(
    tenant: TenantContext, fake_resolve: FakeResolveClient, tmp_path: Path
) -> None:
    """micro 真的跑了（审计事件可证）且模型收到的注入段**逐字与注入原文一致**。

    对照组用**审计事件**而不是去找占位符：`CONTEXT_COMPACTED` 里 `micro.fired=True` 是"这一层
    确实执行过"的直接证据；在此前提下注入段仍然逐字，才说明它没被降级、也没被改写。
    """
    await _seed(tenant)
    budget: dict[str, Any] = {"micro": True, "keep_recent": 0}
    async with _stack(fake_resolve, budget, tmp_path) as (client, probe):
        await _script_tool_round(probe)
        await _send(client, tenant, "第一轮：帮我 dump 一份内容")
        first_tool = _tool_texts(await _last_request(probe))
        assert first_tool and first_tool[-1].startswith('{"artifact"'), "前置条件：结果确实被外置了"

        await _send(client, tenant, "第二轮：接着聊")
        body = await _last_request(probe)

    async with get_session_factory()() as session:
        rows = list(
            (
                await session.execute(
                    select(CanonicalEvent).where(
                        CanonicalEvent.tenant_id == tenant.tenant_id,
                        CanonicalEvent.event_type == "CONTEXT_COMPACTED",
                    )
                )
            )
            .scalars()
            .all()
        )
    fired_layers = {
        layer["layer"]
        for row in rows
        for layer in (row.payload_json or {}).get("layers", {}).values()
        if layer.get("fired")
    }
    assert "micro" in fired_layers, f"micro 必须真的执行过（否则本用例是空转）：{fired_layers}"

    block = _injected_block(body)
    for key, value in MEMORIES.items():
        assert f'"memory_key":"{key}"' in block and f'"value":"{value}"' in block, (
            "注入段落在受保护前缀里：micro 不得把它换掉或改写"
        )
