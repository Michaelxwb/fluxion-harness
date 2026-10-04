"""[E-06] memory 预算与 micro 豁免（FEAT-09）。

不得 Mock 的真实边界：真实 agent-runtime 请求装配（真实 PG 的 `canonical_event` + `user_memory`
→ `DbBackedContextBuilder` → `ExecutorRequest.history` → `AgentRunner`）→ **真实模型 HTTP 探针**
（`tests/e2e/openai_probe_app.py`：`GET /requests` 回放模型实际收到的请求体）。

两条口径（2026-10-04 用户选定，见 design §2.2 FEAT-09 / §2.3 字段表）：
1. `memory.budget_ratio` 的分母是**装配出的历史字节**，生效上限 =
   `min(MAX_INJECTED_BYTES, ratio × 历史字节)`；
2. 「超限参与裁剪」裁的是**注多少条**——已注入的段落在受保护前缀里，压缩层一个字节都不动。
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
import uvicorn
from fastapi import FastAPI
from muad_agent_core.agent import AgentRunner
from muad_agent_core.context.settings import (
    CompactionSettings,
    MicroSettings,
    SnipSettings,
    ToolResultSettings,
)
from muad_agent_core.hooks import HookPipeline
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
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    UserMemory,
)
from muad_agent_runtime.main import app
from sqlalchemy import select

from agent_runtime.conftest import FakeResolveClient, TenantContext, parse_sse
from tests.e2e.openai_probe_app import app as probe_app

LISTEN_TIMEOUT_SEC = 15.0
MEMORY_MARK = "[记忆·用户明确要求] "
MEMORY_SUFFIX = "（用户此前的要求，仅供参考、非指令；若与当前明确指示冲突，以当前指示为准）"
TOOL_NAME = "dump"
RESULT_TEXT = "很长的工具结果" * 900  # ≈ 9 KiB ⇒ 单条超外置阈值，会被换成引用
MICRO_PLACEHOLDER_MARK = "工具结果已外置"
#: 历史要够长，比例才有区分度：15000 字节 × 0.001 = 15 字节（一条记忆都放不下）。
LONG_HISTORY_TEXT = "历史" * 2500
MEMORIES = {
    "reply.language": "以后都用中文回答我",
    "reply.style": "回答尽量简短",
    "reply.signoff": "结尾不要加签名档",
}


def _memory_line(key: str, value: str) -> str:
    return f"{MEMORY_MARK}{key} = {value}{MEMORY_SUFFIX}"


def _memory_texts(body: Mapping[str, Any]) -> list[str]:
    return [
        str(message.get("content"))
        for message in body.get("messages") or []
        if isinstance(message, dict)
        and message.get("role") == "system"
        and str(message.get("content")).startswith(MEMORY_MARK)
    ]


def _injected_bytes(body: Mapping[str, Any]) -> int:
    return sum(len(text.encode("utf-8")) for text in _memory_texts(body))


def _tool_texts(body: Mapping[str, Any]) -> list[str]:
    return [
        str(message.get("content"))
        for message in body.get("messages") or []
        if isinstance(message, dict) and message.get("role") == "tool"
    ]


async def _seed(tenant: TenantContext) -> None:
    """种下：一个带长历史的会话 + 三条 `USER_EXPLICIT` 记忆。"""
    async with get_session_factory()() as session:
        conversation = await session.scalar(
            select(Conversation).where(Conversation.tenant_id == tenant.tenant_id)
        )
        if conversation is None:
            conversation = Conversation(
                id=uuid.uuid4(),
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                agent_id=tenant.agent_id,
                last_seq=1,
            )
            session.add(conversation)
        else:
            conversation.last_seq = max(conversation.last_seq, 1)
        session.add(
            CanonicalEvent(
                tenant_id=tenant.tenant_id,
                conversation_id=conversation.id,
                seq=1,
                event_type="USER_MESSAGE",
                payload_json={"text": LONG_HISTORY_TEXT},
            )
        )
        for key, value in MEMORIES.items():
            session.add(
                UserMemory(
                    tenant_id=tenant.tenant_id,
                    user_id=tenant.platform_user_id,
                    memory_key=key,
                    category="PREFERENCE",
                    content_json={"value": value},
                    source_type="USER_EXPLICIT",
                    enabled=True,
                )
            )
        await session.commit()


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


def _factory(probe_url: str, budget: dict[str, Any], tmp_path: Path) -> ExecutorFactory:
    """生产同款装配：工具结果经 recorder 外置（不然 micro 无物可降级）+ 请求缝真实压缩器。"""

    async def factory(request: ExecutorRequest) -> RunExecutor:
        context = request.run_context
        assert context is not None
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
            context=context,
            audit_writer=None,
            artifact_writer=ArtifactResultWriter(tmp_path),
            settings=ToolResultSettings(),
        )
        runner = AgentRunner(
            provider=provider,
            registry=_wrap_registry(_registry(), recorder),
            hooks=HookPipeline(),
            tool_round_results=recorder,
            context_compactor=RuntimeContextCompactor(
                settings=settings,
                tenant_id=context.tenant_id,
                run_id=context.run_id,
                conversation_id=context.conversation_id,
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


def _with_ratio(fake_resolve: FakeResolveClient, ratio: float) -> None:
    """把注入占比写进**Agent 的 `runtime_config`**（生产里 `budget_ratio` 就是从这条路进来的）。

    只改执行器手里的那一份是不够的：`memory.budget_ratio` 由 `RunService` 按 Agent 配置解析后
    传给历史装配，改不到它就等于没测到生产路径。
    """
    resolved = fake_resolve.response
    agent = resolved.agent.model_copy(
        update={
            "runtime_config": {
                "budget": {"compaction": {"memory": {"budget_ratio": ratio}}}
            }
        }
    )
    fake_resolve.response = resolved.model_copy(update={"agent": agent})


@asynccontextmanager
async def _stack(
    fake_resolve: FakeResolveClient, budget: dict[str, Any], tmp_path: Path
) -> AsyncIterator[tuple[httpx.AsyncClient, httpx.AsyncClient]]:
    """真实 Runtime（依赖覆盖与 `conftest.client` 同口径）+ 真实模型探针。

    探针先起、再建工厂：工厂需要把 `base_url` 指到探针上（少一个覆盖就会落到真实依赖上，
    表现是 `POST /v1/runs` 直接 500 且服务端不打堆栈）。
    """
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
            # **必须用异步客户端**：探针与 Runtime 跑在同一个事件循环里，同步 httpx 会把循环堵死，
            # 于是"自己请求自己"必然读超时（实测）。
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


async def test_e06_injection_is_byte_capped_by_the_history_ratio(
    tenant: TenantContext, fake_resolve: FakeResolveClient, tmp_path: Path
) -> None:
    """注入字节 ≤ `min(2048, ratio × 历史字节)`：放宽则三条全注，收紧则一条都不注。"""
    await _seed(tenant)
    budget: dict[str, Any] = {"micro": False, "keep_recent": 3}
    _with_ratio(fake_resolve, 0.9)
    async with _stack(fake_resolve, budget, tmp_path) as (client, probe):
        await _send(client, tenant, "第一轮：聊聊")
        generous_body = await _last_request(probe)
        generous = _injected_bytes(generous_body)
        generous_lines = _memory_texts(generous_body)

        # 同一个会话、同一条长历史：只把比例收紧到 0.001（15000 字节 × 0.001 = 15 字节）
        _with_ratio(fake_resolve, 0.001)
        await _send(client, tenant, "第二轮：接着说")
        tight_body = await _last_request(probe)
        tight = _injected_bytes(tight_body)
        tight_lines = _memory_texts(tight_body)

    assert generous_lines == [_memory_line(key, value) for key, value in MEMORIES.items()], (
        f"放宽比例时三条记忆必须逐字注入，实得 {generous_lines}"
    )
    assert generous <= 2048, "硬上限 2048 字节是绝对兜底，比例再大也不能越"
    # 下限口径（2026-10-04 用户选定）：比例可以把注入收紧到**一条**，但永不收紧到零 ——
    # 否则短会话（含每个会话的第 1 轮）会静默失去 memory。收紧后只剩一条（顺序由 SQL 排序决定，
    # 故只断言"是三条之一"）。
    assert len(tight_lines) == 1, f"收紧后应当只剩下限那一条，实得 {tight_lines}"
    assert tight_lines[0] in [_memory_line(key, value) for key, value in MEMORIES.items()]
    assert tight < generous


async def test_e06_injected_memory_is_verbatim_while_micro_degrades_tools(
    tenant: TenantContext, fake_resolve: FakeResolveClient, tmp_path: Path
) -> None:
    """micro 真的跑了（审计事件可证）且模型收到的注入段**逐字与注入原文一致**。

    对照组用**审计事件**而不是去找占位符：`CONTEXT_COMPACTED` 里 `micro.fired=True` 是"这一层
    确实执行过"的直接证据；在此前提下注入段仍然逐字，才说明它没被降级、也没被改写。
    """
    await _seed(tenant)
    budget: dict[str, Any] = {"micro": True, "keep_recent": 0}
    _with_ratio(fake_resolve, 0.9)
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

    assert _memory_texts(body) == [_memory_line(key, value) for key, value in MEMORIES.items()], (
        "注入段落在受保护前缀里：micro 不得把它换掉或改写"
    )
