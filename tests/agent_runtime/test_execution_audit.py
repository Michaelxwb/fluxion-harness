"""[B-113/B-117/S-06/E-06] 执行链审计与 Artifact 外置（真实 PostgreSQL + 文件系统）。"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import sqlalchemy as sa
from conftest import FakeExecutor, TenantContext, parse_sse
from httpx import AsyncClient
from muad_agent_core.model import (
    ModelRateLimitedError,
    ModelRequest,
    ModelResponse,
)
from muad_agent_core.tools import ToolDefinition, ToolEffect
from muad_agent_runtime.api.deps import get_executor_factory
from muad_agent_runtime.application.artifacts import ArtifactResultWriter
from muad_agent_runtime.application.executor import (
    AuditedModelProvider,
    ExecutorRequest,
    ExecutorRunContext,
    RunExecutor,
    ToolCallRecorder,
)
from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Artifact,
    ModelInvocationAudit,
    ToolCallAudit,
)
from muad_agent_runtime.main import app

TENANT = f"exec-{uuid.uuid4()}"
RUN_ID = uuid.uuid4()
CONV_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def _writer() -> RuntimeAuditWriter:
    return RuntimeAuditWriter(
        tenant_id=TENANT,
        run_id=RUN_ID,
        task_id=None,
        conversation_id=CONV_ID,
        user_id=USER_ID,
        session_factory=get_session_factory,
    )


@pytest.fixture(autouse=True)
async def _cleanup() -> AsyncIterator[None]:
    yield
    async with get_session_factory()() as session:
        for model in (ToolCallAudit, ModelInvocationAudit, Artifact):
            await session.execute(model.__table__.delete().where(model.tenant_id == TENANT))
        await session.commit()


async def test_tool_recorder_audits_and_externalizes_large_result(tmp_path) -> None:
    """[B-113] 工具执行落 tool_call_audit；大结果外置 Artifact 并返回引用。"""
    recorder = ToolCallRecorder(
        context=ExecutorRunContext(
            tenant_id=TENANT, run_id=RUN_ID, conversation_id=CONV_ID, user_id=USER_ID
        ),
        audit_writer=_writer(),
        artifact_writer=ArtifactResultWriter(tmp_path),
    )
    definition = ToolDefinition(
        name="load_skill",
        description="d",
        input_schema={"type": "object"},
        effect=ToolEffect.READ,
    )
    large = "x" * (9 * 1024)

    async def handler(arguments: Any) -> str:
        return large

    content = await recorder(definition, {"skill_key": "demo"}, handler=handler)
    payload = json.loads(content)
    assert payload["artifact"]["size"] == len(large)
    assert payload["artifact"]["preview"].startswith("x")

    async with get_session_factory()() as session:
        audits = (
            await session.execute(
                sa.select(ToolCallAudit).where(ToolCallAudit.tenant_id == TENANT)
            )
        ).scalars().all()
        assert len(audits) == 1
        assert audits[0].tool_name == "load_skill"
        assert audits[0].tool_kind == "SKILL"
        assert audits[0].status == "OK"
        assert audits[0].artifact_id is not None

        artifacts = (
            await session.execute(
                sa.select(Artifact).where(Artifact.tenant_id == TENANT)
            )
        ).scalars().all()
        assert len(artifacts) == 1
        assert artifacts[0].artifact_type == "TOOL_RESULT"
        assert (tmp_path / artifacts[0].storage_key).is_file()


async def test_tool_recorder_audits_failure_status() -> None:
    recorder = ToolCallRecorder(
        context=ExecutorRunContext(
            tenant_id=TENANT, run_id=RUN_ID, conversation_id=CONV_ID, user_id=USER_ID
        ),
        audit_writer=_writer(),
        artifact_writer=None,
    )
    definition = ToolDefinition(
        name="mcp::demo::search",
        description="d",
        input_schema={"type": "object"},
        effect=ToolEffect.READ,
    )

    async def handler(arguments: Any) -> str:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await recorder(definition, {"q": "x"}, handler=handler)

    async with get_session_factory()() as session:
        audit = (
            await session.execute(
                sa.select(ToolCallAudit).where(ToolCallAudit.tenant_id == TENANT)
            )
        ).scalar_one()
        assert audit.tool_kind == "MCP"
        assert audit.status == "ERROR"
        assert audit.error_code == "COMMON_INTERNAL_ERROR"


class _FlakyProvider:
    def __init__(self, failures: int) -> None:
        self._failures = failures
        self.calls = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls <= self._failures:
            raise ModelRateLimitedError("limited", retry_after=0.0)
        return ModelResponse(content="ok", finish_reason="stop")


async def test_audited_model_provider_records_each_attempt() -> None:
    """[S-06/E-06] 逐 attempt 审计：失败 RETRY + 成功 OK，attempt 递增。"""
    provider = AuditedModelProvider(
        _FlakyProvider(failures=2), _writer(), model="gpt-4o-mini"
    )
    request = ModelRequest(
        model_id="gpt-4o-mini", messages=(), tools=(), temperature=None, max_tokens=None, params={}
    )
    for _ in range(2):
        with pytest.raises(ModelRateLimitedError):
            await provider.complete(request)
    response = await provider.complete(request)
    assert response.content == "ok"

    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.select(ModelInvocationAudit)
                .where(ModelInvocationAudit.tenant_id == TENANT)
                .order_by(ModelInvocationAudit.attempt)
            )
        ).scalars().all()
        assert [(row.attempt, row.status) for row in rows] == [
            (0, "RETRY"),
            (1, "RETRY"),
            (2, "OK"),
        ]
        assert rows[0].retry_reason == "RATE_LIMITED"
        assert rows[-1].error_code is None


async def test_run_request_carries_history_and_credentials_surface(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    """执行请求携带上下文历史与内存凭据；历史含前序 USER/ASSISTANT 轮次。"""
    seen: list[ExecutorRequest] = []

    async def factory(request: ExecutorRequest) -> RunExecutor:
        seen.append(request)
        return FakeExecutor(request)

    app.dependency_overrides[get_executor_factory] = lambda: factory
    payload = {
        "agent_id": str(tenant.agent_id),
        "platform_user_id": str(tenant.platform_user_id),
        "channel": {"type": "WECOM", "bot_id": "bot-1"},
        "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": "first"},
    }
    first = await client.post("/v1/runs", json=payload, headers={"X-Tenant-Id": tenant.tenant_id})
    assert first.status_code == 200
    first_events = parse_sse(first.text)
    assert first_events[-1]["type"] == "run.completed", first.text
    assert first_events[-1]["data"]["status"] == "COMPLETED", first.text
    conversation_id = first_events[0]["data"]["conversation_id"]

    second = await client.post(
        "/v1/runs",
        json={
            **payload,
            "conversation_id": conversation_id,
            "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": "second"},
        },
        headers={"X-Tenant-Id": tenant.tenant_id},
    )
    assert second.status_code == 200

    history = seen[-1].history
    contents = [message.content for message in history]
    assert contents[0] == "first"
    assert "runtime execution engine" in " ".join(contents)  # 第一轮 assistant 回复
    assert contents[-1] == "second"
    assert seen[-1].run_context is not None
    assert seen[-1].run_context.tenant_id == tenant.tenant_id
