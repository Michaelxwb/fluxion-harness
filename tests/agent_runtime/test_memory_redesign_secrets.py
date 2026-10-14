"""B-05 real boundary: secret shapes are refused and the canary never lands in any store.

Owner: TASK-029. 断言口径 = design §2.5.2 B-05 + §3.4.5 内容闸门 + §3.8 日志/审计反查：
value、key、quote、TASK_STATE 任一含密钥形态则拒绝；memory/history/proposal/audit/log 中无 canary。
真实边界：真实写入服务 + 真实 PG（隔离 head 库）+ 真实工具流水线，不 mock 业务存储。
"""

import json
from dataclasses import replace
from importlib import import_module
from uuid import uuid4

import pytest
import sqlalchemy as sa
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, ToolCallAudit
from muad_api import AppError
from muad_contracts.memory import (
    FactContent,
    MemoryCandidate,
    MemoryCategory,
    MemorySourceEvidence,
    RememberMemoryInput,
    TaskStateContent,
    task_memory_key,
)

from tests.agent_runtime.memory_confirmation_support import source
from tests.agent_runtime.memory_redesign_write_support import command, context, upsert, writer

pytestmark = pytest.mark.integration
#: 本次执行已知凭据 canary（真实装配里来自模型 api_key / MCP secret）。
CANARY = "canary-credential-9f3c"
SECRET_TEXTS = (
    "sk-" + "x" * 30,
    "ghp_" + "z" * 30,
    "password=fixture",
    "{'password': 'fixture'}",
    '{"api_key":"opaque"}',
)
SENSITIVE_KEYS = ("color." + CANARY, "color.password", "api-key")
STORE_TABLES = (
    "user_memory",
    "user_memory_history",
    "memory_command_receipt",
    "memory_scope",
    "memory_consolidation_job",
    "run_interrupt",
    "tool_call_audit",
)


def gate():
    return import_module("muad_agent_runtime.application.memory_write")


def commands():
    return import_module("muad_agent_runtime.application.memory_commands")


def _candidate(field, text):
    if field == "task":
        root = uuid4()
        return MemoryCandidate(
            memory_key=task_memory_key(root),
            category=MemoryCategory.TASK_STATE,
            content=TaskStateContent(
                title="task",
                goal=text,
                constraints=[],
                open_items=[],
                next_action="next",
                root_conversation_id=root,
                source_conversation_id=root,
            ),
        )
    if field == "key":
        return MemoryCandidate(
            memory_key=text, category=MemoryCategory.PREFERENCE, content=FactContent(value="blue")
        )
    return MemoryCandidate(
        memory_key="color",
        category=MemoryCategory.PREFERENCE,
        content=FactContent(value=text if field == "value" else "blue"),
    )


async def stored_text(engine, tenant):
    """租户可见的全部存储文本（含 JSONB），canary 反查用。"""
    chunks = []
    async with engine.connect() as connection:
        for table in STORE_TABLES:
            rows = (
                await connection.execute(
                    sa.text(
                        f"SELECT to_jsonb(t) AS payload FROM runtime.{table} t"
                        " WHERE tenant_id = :tenant"
                    ),
                    {"tenant": tenant},
                )
            ).scalars().all()
            chunks.extend(str(row) for row in rows)
    return "\n".join(chunks)


@pytest.mark.parametrize("field", ["value", "quote", "task"])
@pytest.mark.parametrize("text", SECRET_TEXTS + (CANARY,))
def test_b05_memory_redesign_gate_rejects_secret_text_without_echoing_it(field, text):
    with pytest.raises(AppError) as exc:
        gate().validate_memory_content(
            _candidate(field, text),
            source_quote=text if field == "quote" else None,
            credential_canaries=(CANARY,),
        )
    assert exc.value.code == "MEMORY_FORBIDDEN_CONTENT"
    assert text not in str(exc.value) and CANARY not in str(exc.value)


@pytest.mark.parametrize("key", SENSITIVE_KEYS)
def test_b05_memory_redesign_gate_rejects_sensitive_key_names(key):
    with pytest.raises(AppError) as exc:
        gate().validate_memory_content(
            _candidate("key", key), source_quote=None, credential_canaries=(CANARY,)
        )
    assert exc.value.code == "MEMORY_FORBIDDEN_CONTENT"
    assert key not in str(exc.value)


def _mutation(field):
    if field == "value":
        return upsert(value=CANARY)
    if field == "key":
        return upsert(key="color." + CANARY)
    if field == "quote":
        return upsert()
    root = uuid4()
    content = TaskStateContent(
        title="task",
        goal="goal",
        constraints=[CANARY],
        open_items=[],
        next_action="next",
        root_conversation_id=root,
        source_conversation_id=root,
    )
    return commands().MemoryMutation(
        action="UPSERT",
        candidate=MemoryCandidate(
            memory_key=task_memory_key(root), category=MemoryCategory.TASK_STATE, content=content
        ),
    )


def _writer_context(field):
    ctx = replace(context(actor="AGENT"), credential_canaries=(CANARY,))
    if field == "quote":
        return replace(
            ctx,
            evidence=MemorySourceEvidence(
                source_event_id=uuid4(), source_seq=1, source_scope_revision=0, source_quote=CANARY
            ),
        )
    return replace(ctx, source_run_id=uuid4())


@pytest.mark.parametrize("field", ["value", "key", "quote", "task"])
async def test_b05_memory_redesign_writer_rejects_and_stores_no_canary(async_tool_database, caplog, field):
    ctx = _writer_context(field)
    with pytest.raises(AppError) as exc:
        await writer(async_tool_database).execute(ctx, command(_mutation(field)))
    assert exc.value.code == "MEMORY_FORBIDDEN_CONTENT"
    assert CANARY not in caplog.text
    assert CANARY not in await stored_text(async_tool_database, ctx.tenant_id)


async def test_b05_memory_redesign_proposal_rejects_canary_before_any_store(async_tool_database):
    factory, run, identity, submission_id = await source(async_tool_database)
    confirmation = import_module(
        "muad_agent_runtime.application.memory_confirmation"
    ).MemoryConfirmationService(
        factory, identity, submission_id=submission_id, credential_canaries=(CANARY,)
    )
    candidate = RememberMemoryInput(memory_key="color", category="PREFERENCE", value=CANARY)
    with pytest.raises(AppError) as exc:
        await confirmation.propose(candidate, call_id="b05-proposal")
    assert exc.value.code == "MEMORY_FORBIDDEN_CONTENT"
    assert confirmation.suspend_request is None
    assert CANARY not in await stored_text(async_tool_database, run.tenant_id)


async def test_b05_memory_redesign_consolidation_input_and_output_hide_the_canary(async_tool_database):
    from muad_agent_runtime.infrastructure.models.runtime import MemoryConsolidationJob

    from tests.agent_runtime.memory_consolidation_support import completed_job

    input_module = import_module("muad_agent_runtime.application.memory_consolidation_input")
    output_module = import_module("muad_agent_runtime.application.memory_consolidation_output")
    factory, run, _, _ = await source(async_tool_database)
    job, _ = await completed_job(factory, run)
    async with factory.begin() as session:
        row = await session.get(MemoryConsolidationJob, job.id)
        row.through_seq = 2
        session.add(
            CanonicalEvent(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                seq=2,
                event_type="USER_MESSAGE",
                payload_json={"text": f"api_key={CANARY}"},
            )
        )
    async with factory() as session:
        built = await input_module.build_input(session, job, credential_canaries=(CANARY,))
    prompt = json.dumps(built.model_dump(mode="json"), ensure_ascii=False)
    assert CANARY not in prompt and "api_key" not in prompt
    with pytest.raises(output_module.ConsolidationRejected):
        output_module.parse_output(
            json.dumps(
                {
                    "candidates": [
                        {"memory_key": "color", "category": "PREFERENCE", "content": {"value": CANARY}}
                    ],
                    "task_completed": False,
                }
            ),
            finish_reason="stop",
            conversation_id=run.conversation_id,
            root_conversation_id=run.conversation_id,
            max_candidates=8,
            credential_canaries=(CANARY,),
        )


async def test_b05_memory_redesign_tool_audit_preview_redacts_canary(async_tool_database):
    from muad_agent_core.model import ModelMessage, ModelRole
    from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry
    from muad_agent_core.tools.pipeline import ToolExecutionPipeline
    from muad_agent_runtime.application.executor import ExecutorRunContext, ToolCallRecorder
    from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter

    factory, run, _, _ = await source(async_tool_database)
    recorder = ToolCallRecorder(
        context=ExecutorRunContext(
            tenant_id=run.tenant_id, run_id=run.id, conversation_id=run.conversation_id, user_id=run.user_id
        ),
        audit_writer=RuntimeAuditWriter(
            tenant_id=run.tenant_id,
            run_id=run.id,
            task_id=None,
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            session_factory=lambda: factory,
        ),
        artifact_writer=None,
        credential_canaries=(CANARY,),
    )
    registry = ToolRegistry()

    async def handler(arguments, *, call_id):
        return '{"saved":false}'

    registry.register(
        ToolDefinition(
            name="remember",
            description="fixture",
            effect=ToolEffect.WRITE,
            input_schema={
                "type": "object",
                "properties": {
                    key: {"type": "string"} for key in ("memory_key", "category", "value", "source_quote")
                },
                "additionalProperties": False,
            },
            handler=handler,
        )
    )
    pipeline = ToolExecutionPipeline(registry=registry, audit=recorder)
    result = await pipeline.execute(
        pipeline.prepare(
            call_id="b05-audit",
            tool_name="remember",
            arguments={
                "memory_key": "color." + CANARY,
                "category": "PREFERENCE",
                "value": CANARY,
                "source_quote": CANARY,
            },
        )
    )
    await recorder.finish_round(
        [ModelMessage(role=ModelRole.TOOL, content=result.content, tool_call_id="b05-audit")]
    )
    async with factory() as session:
        audit = await session.scalar(sa.select(ToolCallAudit).where(ToolCallAudit.run_id == run.id))
    assert audit is not None and audit.args_preview_json["memory_key"] == "<redacted>"
    assert set(audit.args_preview_json) <= {"memory_key", "category", "args_hash", "status"}
    assert CANARY not in json.dumps(audit.args_preview_json, ensure_ascii=False)
