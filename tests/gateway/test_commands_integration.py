"""B-111: `/skills` 与 `/new` 命令输出（真实 Console HTTP → PostgreSQL → 真实 Runtime HTTP）。

不得 Mock 的真实边界：真实 Console 服务进程（真实 socket + 真实 PostgreSQL 授权查询）+
真实 Runtime HTTP 接收端（记录 `/v1/conversations` 与是否创建 Run）。错误分支复用 B-102
的真实本地 HTTP 服务口径（真实 uvicorn + 真实 socket 返回封套），不 mock Python 对象。
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

_TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(_TESTS_ROOT) not in sys.path:  # 复用 console_channel 的真实 Console/PG 夹具
    sys.path.insert(0, str(_TESTS_ROOT))

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fakes import ConsoleProcess, StubConsole
from muad_api.catalog import MessageCatalog
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import (
    AgentSkillBinding,
    Skill,
    SkillArtifact,
)
from muad_common import SharedSettings
from muad_contracts import ChannelEnvelope
from muad_im_gateway.application.console_client import ConsoleClient
from muad_im_gateway.application.inbound import InboundPipeline
from muad_im_gateway.application.runtime_client import RuntimeClient
from muad_im_gateway.channels.fake import FakeChannelAdapter
from muad_im_gateway.infrastructure.dedupe import DedupeStore, NullDedupeStore, build_dedupe_store
from sqlalchemy import delete

from console_channel.conftest import (  # noqa: F401  (fixture reuse)
    ChannelContext,
    channel,
    database_guard,
)
from console_channel.test_channel_skills_api import _seed_skill  # 复用 B-104 的有效技能种子构造

RESOLVE_PATH = "/internal/channel/resolve"
SKILLS_PATH = "/internal/channel/skills"
CONVERSATIONS_PATH = "/v1/conversations"
RUNS_PATH = "/v1/runs"
READY_TIMEOUT_SEC = 15.0
NO_SKILLS_TEXT = "暂无可用技能"
SKILLS_UNAVAILABLE_TEXT = "技能列表暂不可用"
NEW_CONVERSATION_TEXT = "已创建新会话"
PAGE_SIZE = 20
UNAUTHORIZED_CANARY = "unauthorized-skill-canary-do-not-leak"


class CommandRuntimeReceiver:
    """真实 Runtime HTTP 接收端：记录会话请求、Run 请求与链路头。"""

    def __init__(self) -> None:
        self.conversations: list[dict[str, Any]] = []
        self.conversation_headers: list[dict[str, str]] = []
        self.run_requests: list[dict[str, Any]] = []
        app = FastAPI()

        @app.post(CONVERSATIONS_PATH)
        async def _conversations(request: Request) -> JSONResponse:
            self.conversations.append(json.loads((await request.body()).decode("utf-8")))
            self.conversation_headers.append(dict(request.headers))
            return JSONResponse(
                {
                    "code": "0",
                    "msg": "成功",
                    "data": {"conversation_id": str(uuid.uuid4())},
                }
            )

        @app.post(RUNS_PATH)
        async def _runs(request: Request) -> JSONResponse:
            self.run_requests.append(json.loads((await request.body()).decode("utf-8")))
            return JSONResponse({"code": "0", "msg": "成功", "data": {}})

        self._app = app
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def start(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        config = uvicorn.Config(self._app, host="127.0.0.1", port=port, log_level="error")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + READY_TIMEOUT_SEC
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self._server.started:
            raise RuntimeError("runtime receiver did not start")
        self.url = f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


@pytest.fixture()
async def console_server(tmp_path: Path) -> AsyncIterator[ConsoleProcess]:
    server = ConsoleProcess()
    server.start(tmp_path)
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture()
async def runtime_receiver() -> AsyncIterator[CommandRuntimeReceiver]:
    receiver = CommandRuntimeReceiver()
    receiver.start()
    try:
        yield receiver
    finally:
        receiver.stop()


@pytest.fixture()
async def catalog_env(channel: ChannelContext) -> AsyncIterator[dict[str, Any]]:
    """真实 PG：21 条可见（跨 2 页）+ 1 条未授权；结束硬删除（FK 顺序，不阻塞租户清理）。"""
    visible: list[dict[str, str]] = []
    seeded_ids: list[uuid.UUID] = []
    hidden_name = "未授权技能"
    async with get_session_factory()() as session:
        bindings = []
        for index in range(PAGE_SIZE + 1):
            skill, _artifact = await _seed_skill(
                session,
                channel.tenant_id,
                f"skill-page-{index:02d}-{uuid.uuid4().hex[:6]}",
                user_scope="ALL",
                description=f"描述{index:02d}",
            )
            bindings.append(AgentSkillBinding(agent_id=channel.agent_id, skill_id=skill.id))
            seeded_ids.append(skill.id)
            visible.append(
                {"name": skill.name, "label": str(skill.platform_label), "description": skill.description}
            )
        hidden, _hidden_artifact = await _seed_skill(
            session,
            channel.tenant_id,
            f"skill-hidden-{uuid.uuid4().hex[:6]}",
            user_scope="SELECTED",  # 有绑定但无用户授权 → 不进目录
            description=UNAUTHORIZED_CANARY,
        )
        hidden.name = hidden_name
        bindings.append(AgentSkillBinding(agent_id=channel.agent_id, skill_id=hidden.id))
        seeded_ids.append(hidden.id)
        session.add_all(bindings)
        await session.commit()
    try:
        yield {"visible": visible, "hidden_name": hidden_name}
    finally:
        async with get_session_factory()() as session:
            await session.execute(
                delete(AgentSkillBinding).where(AgentSkillBinding.skill_id.in_(seeded_ids))
            )
            await session.execute(delete(SkillArtifact).where(SkillArtifact.skill_id.in_(seeded_ids)))
            await session.execute(delete(Skill).where(Skill.id.in_(seeded_ids)))
            await session.commit()


def _envelope(text: str, *, message_id: str, channel_context: ChannelContext) -> ChannelEnvelope:
    return ChannelEnvelope(
        channel="WECOM",
        bot_id=channel_context.bot_id,
        external_user_id=channel_context.bound_external_user_id,
        external_conversation_id="conv-b111",
        message_id=message_id,
        text=text,
    )


def _build_stack(
    console_url: str,
    runtime_url: str,
    catalog: MessageCatalog,
    tenant_id: str,
    dedupe: DedupeStore | None = None,
) -> tuple[FakeChannelAdapter, InboundPipeline, ConsoleClient, RuntimeClient]:
    adapter = FakeChannelAdapter()
    console = ConsoleClient(console_url)
    runtime = RuntimeClient(runtime_url)
    pipeline = InboundPipeline(
        dedupe=dedupe or NullDedupeStore(),
        console=console,
        runtime=runtime,
        catalog=catalog,
        tenant_id=tenant_id,
        locale="zh-CN",
    )
    return adapter, pipeline, console, runtime


def _sent_texts(adapter: FakeChannelAdapter) -> list[str]:
    return [message.text for _route, message in adapter.sent]


async def _resolve_platform_user(console_url: str, channel_context: ChannelContext) -> str:
    async with httpx.AsyncClient(base_url=console_url, timeout=5.0) as client:
        response = await client.post(
            RESOLVE_PATH,
            json={
                "channel": "WECOM",
                "bot_id": channel_context.bot_id,
                "external_user_id": channel_context.bound_external_user_id,
            },
            headers={"X-Tenant-Id": channel_context.tenant_id},
        )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["bound"] is True and data["authorized"] is True, data
    return str(data["platform_user_id"])


async def test_b111_skills_lists_full_catalog_without_leaking_unauthorized(
    console_server: ConsoleProcess,
    runtime_receiver: CommandRuntimeReceiver,
    catalog: MessageCatalog,
    catalog_env: dict[str, Any],
    channel: ChannelContext,
) -> None:
    adapter, pipeline, console, runtime = _build_stack(
        console_server.url, runtime_receiver.url, catalog, channel.tenant_id
    )
    try:
        await pipeline.handle(
            adapter, _envelope("/skills", message_id="msg-b111-skills", channel_context=channel)
        )
    finally:
        await console.aclose()
        await runtime.aclose()

    replies = _sent_texts(adapter)
    assert len(replies) == 1
    text = replies[0]
    # 越界页也要读到：21 条可见技能（page_size=20）全部出现，且 name/label/description 都在
    for item in catalog_env["visible"]:
        assert item["name"] in text, item
        assert item["label"] in text, item
        assert item["description"] in text, item
    # 未授权条目既不出现名称也不出现描述（不泄露存在性）
    assert catalog_env["hidden_name"] not in text
    assert UNAUTHORIZED_CANARY not in text
    # 命令不进 LLM
    assert runtime_receiver.run_requests == []


async def test_b111_empty_catalog_differs_from_unavailable(
    console_server: ConsoleProcess,
    runtime_receiver: CommandRuntimeReceiver,
    catalog: MessageCatalog,
    channel: ChannelContext,
) -> None:
    adapter, pipeline, console, runtime = _build_stack(
        console_server.url, runtime_receiver.url, catalog, channel.tenant_id
    )
    try:
        await pipeline.handle(
            adapter, _envelope("/skills", message_id="msg-b111-empty", channel_context=channel)
        )
    finally:
        await console.aclose()
        await runtime.aclose()
    assert _sent_texts(adapter) == [NO_SKILLS_TEXT]  # 真实 Console/真实 PG：目录为空

    # 错误（404 封套）不得伪装成空目录：真实本地 HTTP 服务返回错误封套
    console_stub = StubConsole()
    console_stub.start()
    console_stub.json_response(
        RESOLVE_PATH,
        {
            "code": "0",
            "msg": "成功",
            "data": {
                "bound": True,
                "agent_id": str(channel.agent_id),
                "platform_user_id": str(uuid.uuid4()),
                "authorized": True,
            },
        },
    )
    console_stub.json_response(
        SKILLS_PATH, {"code": "COMMON_INTERNAL_ERROR", "msg": "broken"}, status_code=404
    )
    adapter2, pipeline2, console2, runtime2 = _build_stack(
        console_stub.url, runtime_receiver.url, catalog, channel.tenant_id
    )
    try:
        await pipeline2.handle(
            adapter2, _envelope("/skills", message_id="msg-b111-broken", channel_context=channel)
        )
    finally:
        await console2.aclose()
        await runtime2.aclose()
        console_stub.stop()

    assert _sent_texts(adapter2) == [SKILLS_UNAVAILABLE_TEXT]
    assert SKILLS_UNAVAILABLE_TEXT != NO_SKILLS_TEXT
    assert runtime_receiver.run_requests == []


async def test_b111_new_creates_conversation_with_stable_key_and_keeps_binding(
    console_server: ConsoleProcess,
    runtime_receiver: CommandRuntimeReceiver,
    catalog: MessageCatalog,
    channel: ChannelContext,
) -> None:
    platform_user_id = await _resolve_platform_user(console_server.url, channel)
    dedupe = await build_dedupe_store(SharedSettings().redis_url)  # 真实去重存储：重试被网关挡下
    message_id = f"msg-b111-new-{uuid.uuid4().hex[:8]}"
    adapter, pipeline, console, runtime = _build_stack(
        console_server.url, runtime_receiver.url, catalog, channel.tenant_id, dedupe
    )
    try:
        await pipeline.handle(
            adapter, _envelope("/new", message_id=message_id, channel_context=channel)
        )
        # 同一命令重试（同 channel message_id）：不得创建第二个会话、不得二次回复
        await pipeline.handle(
            adapter, _envelope("/new", message_id=message_id, channel_context=channel)
        )
    finally:
        await console.aclose()
        await runtime.aclose()
        await dedupe.aclose()

    assert _sent_texts(adapter) == [NEW_CONVERSATION_TEXT]
    assert len(runtime_receiver.conversations) == 1
    body = runtime_receiver.conversations[0]
    assert body["agent_id"] == str(channel.agent_id)
    assert body["platform_user_id"] == platform_user_id
    # 稳定幂等键：本命令的 channel message_id（Runtime Owner 侧重放不建第二会话）
    assert runtime_receiver.conversation_headers[0]["idempotency-key"] == message_id
    # 命令不进 LLM：只调用会话端点
    assert runtime_receiver.run_requests == []
    # 旧绑定不变：真实 PG 的身份解析结果仍是同一 platform_user
    assert await _resolve_platform_user(console_server.url, channel) == platform_user_id
