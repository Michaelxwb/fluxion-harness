"""[E-08] 外置过的工具结果能从 canonical 行**逐字节**重建（ADR-05）。

不得 Mock 的真实边界：真实 `RunService` 建 Run + 真实 SSE 消费 → 真实 `AgentRunner` 工具回合 →
生产同款 `ToolCallRecorder` → 真实 PostgreSQL（`runtime.canonical_event` / `runtime.artifact`）
→ 真实产物根（`tmp_path` 当共享盘）。

场景是**同一会话的两连 Run**：第一个 Run 的工具结果被外置，第二个 Run 重建历史时必须拿回
**当时那条引用 JSON**（含 `artifact_id`），否则模型既看不到内容、也没有 id 去 `read_attachment`。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_core.agent import AgentRunner
from muad_agent_core.hooks import HookPipeline
from muad_agent_core.model import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelToolCall,
    text_of,
)
from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry
from muad_agent_runtime.api.deps import get_executor_factory
from muad_agent_runtime.application.attachments.tool_results import ArtifactResultWriter
from muad_agent_runtime.application.executor import (
    AgentRunnerExecutor,
    ExecutorFactory,
    ExecutorRequest,
    RunExecutor,
    ToolCallRecorder,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Artifact, CanonicalEvent
from muad_agent_runtime.main import app
from muad_contracts.platform_settings import ToolResultSettings

from agent_runtime.conftest import TenantContext, parse_sse

TOOL_NAME = "dump"
CALL_ID = "call-dump"
#: 单条就超外置阈值（8 KiB）⇒ 走"单条必落盘"那条腿，与本用例的整轮预算无关。
RESULT_TEXT = "内容" * 4000
FIRST_TEXT = "第一轮：我去取一下"
SECOND_TEXT = "第二轮：接着聊"
DIRTY_TOOL_NAME = "dirty"
DIRTY_CALL_ID = "call-dirty"


class _TwoRunProvider:
    """第一轮（Run 1）先调工具再收尾；第二轮（Run 2）直接收尾。"""

    def __init__(self, *, first_tool: str = TOOL_NAME, first_call_id: str = CALL_ID) -> None:
        self.requests: list[ModelRequest] = []
        self._first_tool = first_tool
        self._first_call_id = first_call_id

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModelResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=(
                    ModelToolCall(id=self._first_call_id, name=self._first_tool, arguments={}),
                ),
            )
        return ModelResponse(
            content=FIRST_TEXT if len(self.requests) == 2 else SECOND_TEXT,
            finish_reason="stop",
        )


#: 工具自己写出来的"形状对、值脏"的引用：`_artifact_ref` 只校验 str，不校验 UUID。
DIRTY_RESULT = json.dumps({"artifact": {"artifact_id": "not-a-uuid"}})


def _registry(recorder: ToolCallRecorder) -> ToolRegistry:
    registry = ToolRegistry()
    results = {TOOL_NAME: RESULT_TEXT, DIRTY_TOOL_NAME: DIRTY_RESULT}
    for name, result_text in results.items():

        async def handler(
            arguments: Mapping[str, Any], *, call_id: str, _text: str = result_text
        ) -> str:
            return _text

        registry.register(
            ToolDefinition(
                name=name,
                description=f"{name} tool",
                input_schema={"type": "object", "properties": {}},
                effect=ToolEffect.READ,
                handler=handler,
            )
        )
    wrapped = ToolRegistry()
    for definition in registry.list():
        handler_fn = definition.handler
        assert handler_fn is not None
        wrapped.register(
            replace(definition, handler=partial(recorder, definition, handler=handler_fn))
        )
    return wrapped


def _factory(tmp_path: Path, provider: _TwoRunProvider) -> ExecutorFactory:
    async def factory(request: ExecutorRequest) -> RunExecutor:
        context = request.run_context
        assert context is not None
        recorder = ToolCallRecorder(
            context=context,
            audit_writer=None,
            artifact_writer=ArtifactResultWriter(tmp_path),
            settings=ToolResultSettings(),
        )
        runner = AgentRunner(
            provider=provider,
            registry=_registry(recorder),
            hooks=HookPipeline(),
            tool_round_results=recorder,
        )
        return AgentRunnerExecutor(runner=runner, request=request)

    return factory


async def _send(client: AsyncClient, tenant: TenantContext, text: str) -> dict[str, Any]:
    response = await client.post(
        "/v1/runs",
        json={
            "agent_id": str(tenant.agent_id),
            "platform_user_id": str(tenant.platform_user_id),
            "channel": {"type": "WECOM", "bot_id": "bot-history"},
            "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": text},
        },
        headers={"X-Tenant-Id": tenant.tenant_id},
    )
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    assert events[-1]["type"] == "run.completed", json.dumps(events[-1], ensure_ascii=False)
    return {"run_id": uuid.UUID(events[0]["run_id"]), "final_text": events[-1]["data"]["final_text"]}


def _tool_content(request: ModelRequest, call_id: str) -> str:
    matches = [message for message in request.messages if message.tool_call_id == call_id]
    assert matches, f"模型请求里没有 {call_id} 的工具结果"
    return text_of(matches[-1].content)


def _tool_messages(request: ModelRequest) -> list[ModelMessage]:
    return [message for message in request.messages if message.role is ModelRole.TOOL]


@pytest.fixture(autouse=True)
async def _cleanup(tenant: TenantContext) -> AsyncIterator[None]:
    yield
    async with get_session_factory()() as session:
        for model in (Artifact, CanonicalEvent):
            await session.execute(model.__table__.delete().where(model.tenant_id == tenant.tenant_id))
        await session.commit()


async def test_e08_externalized_result_rebuilds_byte_identically(
    client: AsyncClient,
    tenant: TenantContext,
    tmp_path: Path,
) -> None:
    """Run 1 外置的工具结果，在 Run 2 的历史里逐字节还原成当时那条引用 JSON。"""
    provider = _TwoRunProvider()
    app.dependency_overrides[get_executor_factory] = lambda: _factory(tmp_path, provider)
    try:
        first = await _send(client, tenant, "第一轮：帮我 dump 一份内容")
        assert first["final_text"] == FIRST_TEXT
        second = await _send(client, tenant, "第二轮：接着聊")
        assert second["final_text"] == SECOND_TEXT
    finally:
        app.dependency_overrides.pop(get_executor_factory, None)

    # 前置条件：两个 Run 确实在**同一个会话**里（否则下面的重建断言会以另一种方式"通过"）
    assert len(provider.requests) == 3, "Run 1 两次模型调用 + Run 2 一次"
    sent_by_run_one = _tool_content(provider.requests[1], CALL_ID)
    rebuilt = _tool_content(provider.requests[2], CALL_ID)
    assert any(
        "第一轮：帮我 dump 一份内容" in text_of(message.content)
        for message in provider.requests[2].messages
    ), "Run 2 必须带着 Run 1 的历史（同一会话）"

    # ① 当时发出去的就是引用 JSON，不是正文
    payload = json.loads(sent_by_run_one)
    assert payload["artifact"]["preview"], "引用里必须带预览"
    assert len(sent_by_run_one.encode("utf-8")) < len(RESULT_TEXT.encode("utf-8"))

    # ② 重建的那份与当时发出去的那份**逐字节一致**（含 artifact_id，模型下一轮才找得到产物）
    assert rebuilt == sent_by_run_one, "跨 Run 重建必须逐字节还原当时那条引用"

    # ③ canonical 行的 artifact_id **列**已写入，且指向真实产物
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.select(CanonicalEvent).where(
                    CanonicalEvent.tenant_id == tenant.tenant_id,
                    CanonicalEvent.event_type == "TOOL_CALL",
                    CanonicalEvent.run_id == first["run_id"],
                )
            )
        ).scalars().one()
        artifact = await session.get(Artifact, row.artifact_id) if row.artifact_id else None
    assert row.artifact_id is not None, "canonical 行的 artifact_id 列必须是写入的（重建按列取产物）"
    assert artifact is not None and (tmp_path / artifact.storage_key).is_file()


async def test_e08_dirty_artifact_id_does_not_break_the_run(
    client: AsyncClient,
    tenant: TenantContext,
    tmp_path: Path,
) -> None:
    """工具自己写的脏 id：按"没有产物"处理，Run 不受影响、列留空，重建也不凭空造引用。"""
    provider = _TwoRunProvider(first_tool=DIRTY_TOOL_NAME, first_call_id=DIRTY_CALL_ID)
    app.dependency_overrides[get_executor_factory] = lambda: _factory(tmp_path, provider)
    try:
        result = await _send(client, tenant, "第一轮：给我一个脏 id")
        # 同一个会话的下一个 Run：没有产物可指 ⇒ 只留工具名，**不得凭空造引用**
        second = await _send(client, tenant, "第二轮：接着聊")
    finally:
        app.dependency_overrides.pop(get_executor_factory, None)

    assert result["final_text"] == FIRST_TEXT, "一个坏 id 不该让 Run 挂掉"
    assert _tool_content(provider.requests[1], DIRTY_CALL_ID) == DIRTY_RESULT
    assert second["final_text"] == SECOND_TEXT
    assert _tool_content(provider.requests[2], DIRTY_CALL_ID) == f"[tool:{DIRTY_TOOL_NAME}]"
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.select(CanonicalEvent).where(
                    CanonicalEvent.tenant_id == tenant.tenant_id,
                    CanonicalEvent.event_type == "TOOL_CALL",
                    CanonicalEvent.run_id == result["run_id"],
                )
            )
        ).scalars().one()
    assert row.artifact_id is None, "解析不出 UUID ⇒ 按\"没有产物\"计，列留空"
