"""[E-07] 工具结果的**整轮批次预算**真的接进了回合循环（FEAT-02）。

不得 Mock 的真实边界：真实 `RunService` 的 Run 创建与 SSE 消费 → 真实 `AgentRunner` 的
工具回合循环 → 生产同款 `ToolCallRecorder` → 真实 PostgreSQL（`runtime.artifact` /
`runtime.canonical_event` / `runtime.tool_call_audit`）→ 真实产物根（`tmp_path` 当共享盘）。

场景：**一个回合里三条结果，每条都没超单条阈值，但合计超整轮预算** —— 这正是逐条判定看不见、
只有整轮判定才处理得了的那一类（FEAT-02 的主场景）。
"""

from __future__ import annotations

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
from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Artifact, CanonicalEvent, ToolCallAudit
from muad_agent_runtime.main import app
from muad_contracts.platform_settings import ToolResultSettings

from agent_runtime.conftest import TenantContext, parse_sse

#: 三条结果各自都**没超**单条阈值（8 KiB），合计 10.2 KiB 却超了下面这个整轮预算。
SIZES = {"alpha": 4000, "beta": 3500, "gamma": 3000}
ROUND_BUDGET_BYTES = 5_000
PERSIST_THRESHOLD_BYTES = 8 * 1024
CALL_IDS = {name: f"call-{name}" for name in SIZES}
FINAL_TEXT = "核对完毕"


class _ThreeToolProvider:
    """第一轮返回三条工具调用，收到结果后给最终文本。"""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModelResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=tuple(
                    ModelToolCall(id=CALL_IDS[name], name=name, arguments={})
                    for name in SIZES
                ),
            )
        return ModelResponse(content=FINAL_TEXT, finish_reason="stop")


def _tool_message(request: ModelRequest, call_id: str) -> ModelMessage:
    matches = [message for message in request.messages if message.tool_call_id == call_id]
    assert matches, f"模型请求里没有 {call_id} 的工具结果"
    return matches[-1]


def _wrapped_registry(recorder: ToolCallRecorder) -> ToolRegistry:
    """生产同款：每个工具处理函数都经 recorder 包装（逐条缓冲，回合末收口）。"""
    registry = ToolRegistry()
    for name, size in SIZES.items():

        async def handler(arguments: Mapping[str, Any], *, call_id: str, _size: int = size) -> str:
            return "x" * _size

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
        handler = definition.handler
        assert handler is not None
        wrapped.register(replace(definition, handler=partial(recorder, definition, handler=handler)))
    return wrapped


def _settings(*, round_budget_bytes: int = ROUND_BUDGET_BYTES) -> ToolResultSettings:
    return ToolResultSettings(
        persist_threshold_bytes=PERSIST_THRESHOLD_BYTES,
        round_budget_bytes=round_budget_bytes,
    )


def _factory(
    tmp_path: Path,
    provider: _ThreeToolProvider,
    *,
    settings: ToolResultSettings | None = None,
    writer: ArtifactResultWriter | None = None,
) -> ExecutorFactory:
    """按生产同款装配：注册表与 `AgentRunner` 共用**同一个** recorder（ADR-04）。"""

    async def factory(request: ExecutorRequest) -> RunExecutor:
        context = request.run_context
        assert context is not None
        recorder = ToolCallRecorder(
            context=context,
            audit_writer=RuntimeAuditWriter(
                tenant_id=context.tenant_id,
                run_id=context.run_id,
                task_id=None,
                conversation_id=context.conversation_id,
                user_id=context.user_id,
                session_factory=get_session_factory,
            ),
            artifact_writer=writer or ArtifactResultWriter(tmp_path),
            settings=settings or _settings(),
        )
        runner = AgentRunner(
            provider=provider,
            registry=_wrapped_registry(recorder),
            hooks=HookPipeline(),
            tool_round_results=recorder,
        )
        return AgentRunnerExecutor(runner=runner, request=request)

    return factory


@pytest.fixture
def three_tool_provider() -> _ThreeToolProvider:
    return _ThreeToolProvider()


async def _run(client: AsyncClient, tenant: TenantContext) -> tuple[list[dict[str, Any]], uuid.UUID]:
    response = await client.post(
        "/v1/runs",
        json={
            "agent_id": str(tenant.agent_id),
            "platform_user_id": str(tenant.platform_user_id),
            "channel": {"type": "WECOM", "bot_id": "bot-round"},
            "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": "核对一下"},
        },
        headers={"X-Tenant-Id": tenant.tenant_id},
    )
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    run_id = uuid.UUID(events[0]["run_id"])
    return events, run_id


async def _artifacts(tenant_id: str) -> list[Artifact]:
    async with get_session_factory()() as session:
        return list(
            (
                await session.execute(
                    sa.select(Artifact).where(Artifact.tenant_id == tenant_id)
                )
            )
            .scalars()
            .all()
        )


@pytest.fixture(autouse=True)
async def _cleanup(tenant: TenantContext) -> AsyncIterator[None]:
    yield
    async with get_session_factory()() as session:
        for model in (Artifact, ToolCallAudit, CanonicalEvent):
            await session.execute(model.__table__.delete().where(model.tenant_id == tenant.tenant_id))
        await session.commit()


async def test_e07_round_batch_lands_in_the_tool_loop(
    client: AsyncClient,
    tenant: TenantContext,
    three_tool_provider: _ThreeToolProvider,
    tmp_path: Path,
) -> None:
    """单条都没超阈值、合计超整轮预算 ⇒ 从大到小落盘，直到进预算；模型收到引用。"""
    app.dependency_overrides[get_executor_factory] = lambda: _factory(tmp_path, three_tool_provider)
    try:
        events, run_id = await _run(client, tenant)
    finally:
        app.dependency_overrides.pop(get_executor_factory, None)

    assert events[-1]["type"] == "run.completed"
    assert events[-1]["data"]["final_text"] == FINAL_TEXT

    # ① 落盘的是**最大的两条**（4000 + 3500 ⇒ 剩下 3000 ≤ 预算 5000，停）
    stored = await _artifacts(tenant.tenant_id)
    assert sorted(row.size for row in stored) == [3500, 4000], "整轮预算按字节从大到小取舍"
    for row in stored:
        assert (tmp_path / row.storage_key).is_file(), "产物必须真的落在共享盘上"

    # ② 模型实际收到了什么：落盘的两条是引用 JSON，没落盘的那条仍是正文
    second = three_tool_provider.requests[-1]
    for name, size in SIZES.items():
        content = text_of(_tool_message(second, CALL_IDS[name]).content)
        if size >= 3500:
            assert content.startswith('{"artifact"'), f"{name} 应被换成引用"
        else:
            assert content == "x" * size, f"{name} 未超预算，正文必须原样保留"

    # ③ 产物 id 赶上了 canonical 行（`tool.completed` 的载荷就是它的载体）
    async with get_session_factory()() as session:
        rows = list(
            (
                await session.execute(
                    sa.select(CanonicalEvent).where(
                        CanonicalEvent.tenant_id == tenant.tenant_id,
                        CanonicalEvent.event_type == "TOOL_CALL",
                        CanonicalEvent.run_id == run_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    reported = {
        str(row.payload_json.get("artifact_id")) for row in rows if row.payload_json.get("artifact_id")
    }
    assert reported == {str(row.id) for row in stored}, "canonical 行里的产物 id 必须与落盘的一致"

    # ④ 审计行带同一个 id（跨 Run 重建靠它把产物找回来）
    async with get_session_factory()() as session:
        audits = list(
            (
                await session.execute(
                    sa.select(ToolCallAudit).where(ToolCallAudit.tenant_id == tenant.tenant_id)
                )
            )
            .scalars()
            .all()
        )
    assert len(audits) == 3, "三条调用都要有审计行"
    audited = {str(row.artifact_id) for row in audits if row.artifact_id is not None}
    assert audited == {str(row.id) for row in stored}


async def test_e07_round_within_budget_persists_nothing(
    client: AsyncClient,
    tenant: TenantContext,
    three_tool_provider: _ThreeToolProvider,
    tmp_path: Path,
) -> None:
    """反向腿：整轮合计**没超**预算 ⇒ 一条都不落盘（不得因为"整轮判定"把内联结果无谓外置）。"""
    # 把整轮预算抬到合计之上：这一轮不该落任何东西
    generous = _settings(round_budget_bytes=sum(SIZES.values()) + 1)
    app.dependency_overrides[get_executor_factory] = lambda: _factory(
        tmp_path, three_tool_provider, settings=generous
    )
    try:
        events, _ = await _run(client, tenant)
    finally:
        app.dependency_overrides.pop(get_executor_factory, None)

    assert events[-1]["type"] == "run.completed"
    assert await _artifacts(tenant.tenant_id) == []
    second = three_tool_provider.requests[-1]
    for name, size in SIZES.items():
        assert text_of(_tool_message(second, CALL_IDS[name]).content) == "x" * size


async def test_e07_failed_batch_rolls_back_without_failing_the_run(
    client: AsyncClient,
    tenant: TenantContext,
    three_tool_provider: _ThreeToolProvider,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失败注入：整批落盘**中途**炸 ⇒ 盘上不留半批、Run 不失败、模型仍拿到正文。

    注入点在第三件产物的写盘：前两件已经真的写上盘了，所以这条用例验的是**回滚**而不是
    "压根没开始写"。三条都要落盘时 `select_round_persists` 才会选中全部三件，故这里把整轮预算
    压到最小。
    """
    writer = ArtifactResultWriter(tmp_path)
    original = writer._write_immutable  # noqa: SLF001 —— 故意注入中途失败
    calls = {"n": 0}

    def flaky(path: Path, data: bytes) -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("artifact store unreachable")
        original(path, data)

    monkeypatch.setattr(writer, "_write_immutable", flaky)
    app.dependency_overrides[get_executor_factory] = lambda: _factory(
        tmp_path, three_tool_provider, settings=_settings(round_budget_bytes=0), writer=writer
    )
    try:
        events, _ = await _run(client, tenant)
    finally:
        app.dependency_overrides.pop(get_executor_factory, None)

    assert events[-1]["type"] == "run.completed", "外置失败不得让 Run 失败"
    assert events[-1]["data"]["final_text"] == FINAL_TEXT
    assert await _artifacts(tenant.tenant_id) == []
    assert not [path for path in tmp_path.rglob("*") if path.is_file()], "回滚后盘上不留半截产物"
    second = three_tool_provider.requests[-1]
    for name, size in SIZES.items():
        assert text_of(_tool_message(second, CALL_IDS[name]).content) == "x" * size
