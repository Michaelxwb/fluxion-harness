"""[B-113/B-117/S-06/E-06] 执行链审计与 Artifact 外置（真实 PostgreSQL + 文件系统）。"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_core.model import (
    ModelRateLimitedError,
    ModelRequest,
    ModelResponse,
)
from muad_agent_core.tools import ToolDefinition, ToolEffect
from muad_agent_runtime.api.deps import get_executor_factory
from muad_agent_runtime.application.attachments.tool_results import ArtifactResultWriter
from muad_agent_runtime.application.executor import (
    MAX_INLINE_RESULT_BYTES,
    TOOL_RESULT_ARTIFACT_BYTES,
    AuditedModelProvider,
    ExecutorRequest,
    ExecutorRunContext,
    RunExecutor,
    ToolCallRecorder,
    _preview_args,
)
from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Artifact,
    ModelInvocationAudit,
    ToolCallAudit,
)
from muad_agent_runtime.main import app

from agent_runtime.conftest import FakeExecutor, TenantContext, parse_sse

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
        name="demo_tool",
        description="d",
        input_schema={"type": "object"},
        effect=ToolEffect.READ,
    )
    large = "x" * (9 * 1024)

    async def handler(arguments: Any, *, call_id: str) -> str:
        return large

    content = await recorder(definition, {"q": "x"}, handler=handler, call_id="call-demo-1")
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
        assert audits[0].tool_call_id == "call-demo-1"  # 必须是模型给的调用 id，不是工具名
        assert audits[0].tool_name == "demo_tool"
        # `_tool_kind` 现状口径：非 `mcp::` 前缀一律归 SKILL
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


async def test_content_delivery_tool_result_is_not_externalized(tmp_path) -> None:
    """[回归 2026-10-01] 声明 `externalizable_result=False` 的「内容投递」工具，结果不得外置。

    事故现场：`load_skill` 返回 8320 字节（> 8KB 外置阈值）被换成 `{"artifact": {...}}` 与
    400 字符预览，模型只看到正文的 1/20，**且没有任何报错**。而当时的
    `test_tool_recorder_audits_and_externalizes_large_result` 恰好借用 `load_skill` 当夹具名
    并断言它**被外置** —— 测试在为正相反的行为背书，这才是规则冲突没被发现的原因。
    """
    recorder = ToolCallRecorder(
        context=ExecutorRunContext(
            tenant_id=TENANT, run_id=RUN_ID, conversation_id=CONV_ID, user_id=USER_ID
        ),
        audit_writer=_writer(),
        artifact_writer=ArtifactResultWriter(tmp_path),
    )
    definition = ToolDefinition(
        name="demo_tool",
        description="d",
        input_schema={"type": "object"},
        effect=ToolEffect.READ,
        externalizable_result=False,
    )
    large = "x" * (TOOL_RESULT_ARTIFACT_BYTES + 256)  # 前置条件：确实超过通用外置阈值

    async def handler(arguments: Any, *, call_id: str) -> str:
        return large

    content = await recorder(definition, {"q": "x"}, handler=handler, call_id="call-inline-1")

    assert content == large
    assert "artifact" not in content

    async with get_session_factory()() as session:
        artifact_count = await session.scalar(
            sa.select(sa.func.count()).select_from(Artifact).where(Artifact.tenant_id == TENANT)
        )
        assert artifact_count == 0
        audit = (
            await session.execute(
                sa.select(ToolCallAudit).where(ToolCallAudit.tenant_id == TENANT)
            )
        ).scalar_one()
        assert audit.artifact_id is None


async def test_content_delivery_tool_result_is_externalized_above_inline_cap(tmp_path) -> None:
    """「不可外置」不等于「无限直通」：超过内联上限仍然外置。

    没有这道兜底，一个超大 SKILL.md 会直接打爆上下文 —— 而唯一拦着它的就是这条上限。
    它同时把「渐进式披露」从建议变成约束：正文放 SKILL.md，大段规范放 references/。
    """
    recorder = ToolCallRecorder(
        context=ExecutorRunContext(
            tenant_id=TENANT, run_id=RUN_ID, conversation_id=CONV_ID, user_id=USER_ID
        ),
        audit_writer=_writer(),
        artifact_writer=ArtifactResultWriter(tmp_path),
    )
    definition = ToolDefinition(
        name="demo_tool",
        description="d",
        input_schema={"type": "object"},
        effect=ToolEffect.READ,
        externalizable_result=False,
    )
    huge = "x" * (MAX_INLINE_RESULT_BYTES + 1)

    async def handler(arguments: Any, *, call_id: str) -> str:
        return huge

    content = await recorder(definition, {"q": "x"}, handler=handler, call_id="call-inline-2")

    payload = json.loads(content)
    assert payload["artifact"]["size"] == len(huge)


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

    async def handler(arguments: Any, *, call_id: str) -> str:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await recorder(definition, {"q": "x"}, handler=handler, call_id="call-demo-2")

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


async def test_tool_recorder_keeps_repeated_same_tool_calls_distinct() -> None:
    """同一工具在一次 Run 内被调用多次：每次都须落一行，且 tool_call_id 各不相同。

    回归（2026-10-01 排查）：此前用**工具名**充当 `tool_call_id`，而
    `runtime.tool_call_audit` 上有 `UNIQUE (run_id, tool_call_id)`（`0002` 迁移）
    ⇒ 同名工具第二次调用必定撞唯一约束。又因审计写在 `finally` 里，异常还会把工具
    本身的成功结果一并掩盖 —— 真实表现是模型第二次 `load_skill` 直接失败、
    skill 整个用不了（此前用例只断言 `tool_name`，所以从未触发）。
    """
    recorder = ToolCallRecorder(
        context=ExecutorRunContext(
            tenant_id=TENANT, run_id=RUN_ID, conversation_id=CONV_ID, user_id=USER_ID
        ),
        audit_writer=_writer(),
        artifact_writer=None,
    )
    definition = ToolDefinition(
        name="load_skill",
        description="d",
        input_schema={"type": "object"},
        effect=ToolEffect.READ,
    )

    async def handler(arguments: Any, *, call_id: str) -> str:
        return f"ok:{arguments['skill_key']}"

    first = await recorder(definition, {"skill_key": "a"}, handler=handler, call_id="call-a")
    second = await recorder(definition, {"skill_key": "b"}, handler=handler, call_id="call-b")
    assert (first, second) == ("ok:a", "ok:b")

    async with get_session_factory()() as session:
        rows = (
            (
                await session.execute(
                    sa.select(ToolCallAudit)
                    .where(ToolCallAudit.tenant_id == TENANT)
                    .order_by(ToolCallAudit.create_time)
                )
            )
            .scalars()
            .all()
        )
    assert [row.tool_call_id for row in rows] == ["call-a", "call-b"]
    assert {row.tool_name for row in rows} == {"load_skill"}
    assert {row.status for row in rows} == {"OK"}


def test_tool_args_preview_is_redacted_before_it_reaches_the_audit_table() -> None:
    """入参预览落 `runtime.tool_call_audit` 之前**必须过脱敏**。

    预览装的是**模型自己写的内容**：`write_artifact(content=...)`、
    `create_archive(files=[{content: ...}])` 都会把正文开头塞进来。模型完全可能在文件里写一段
    带 `api_key=…`/`token=…` 的配置，而那 200 字符会直接落进审计表——`RULE-secret-001`
    要求密钥不得进入日志与审计。这里复用**同一套** `muad_logging.redaction` 策略。

    同时断言**非敏感内容不受影响**：脱敏只该改它认得的键值对，不该顺手改动正文。
    """
    preview = _preview_args(
        {
            "content": "配置如下：\napi_key=sk-live-abcdef123456\n其余正文照旧",
            "filename": "notes.md",
        }
    )

    assert "sk-live-abcdef123456" not in preview["content"], "密钥落进了审计预览"
    assert "***" in preview["content"], "脱敏没有生效"
    assert "其余正文照旧" in preview["content"], "非敏感正文不得被改动"
    assert preview["filename"] == "notes.md", "不含敏感键的值不得被改动"
