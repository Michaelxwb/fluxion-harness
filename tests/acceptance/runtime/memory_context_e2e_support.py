"""Real HTTP model responses and real canonical history for deferred E-01."""

import json
from uuid import uuid4

from fastapi import FastAPI, Request
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.infrastructure.models.runtime import Artifact, CanonicalEvent, RunRecord
from muad_console_platform.infrastructure.models.control import (
    AgentDefinition,
    ModelCredential,
    ModelDefinition,
    ModelDeployment,
)
from sqlalchemy import select

from tests.acceptance.runtime.conftest import run_db
from tests.agent_runtime.memory_redesign_write_support import command, commands, context, upsert


async def _member_credential(session, model_id):
    """该模型的成员凭据行：运行期连接/密钥在这里（逻辑模型已无旧执行列）。"""
    return (
        await session.scalars(
            select(ModelCredential)
            .join(ModelDeployment, ModelDeployment.credential_id == ModelCredential.id)
            .where(ModelDeployment.model_id == model_id, ModelDeployment.is_deleted.is_(False))
        )
    ).one()

SUMMARY = {"user_goal": "保留摘要诉求", "constraints": [], "progress": [], "open_items": [], "artifacts": []}
#: 后台提炼（consolidation）请求的识别标记与空批次：本场景只验证业务回合与压缩层，
#: 提炼请求若不单独处理，会被计成 primary 业务请求并干扰 `len(primary)` 断言（实测 3 == 2）。
CONSOLIDATION_MARKER = "Consolidate this frozen conversation input"
EMPTY_BATCH = '{"candidates": [], "task_completed": false}'


def _is_consolidation(body):
    return any(
        isinstance(message, dict)
        and message.get("role") == "system"
        and CONSOLIDATION_MARKER in str(message.get("content") or "")
        for message in body.get("messages") or []
    )


def model_probe(stack, key):
    app, bodies, primary = FastAPI(), [], []

    @app.post("/v1/chat/completions")
    async def completion(request: Request):
        body = await request.json()
        bodies.append(body)
        summary = "恰好" in str(body["messages"][-1].get("content"))
        if _is_consolidation(body):
            message = {"role": "assistant", "content": EMPTY_BATCH}
        elif summary:
            message = {"role": "assistant", "content": json.dumps(SUMMARY, ensure_ascii=False)}
        else:
            primary.append(body)
            if len(primary) == 1:
                await delete_fact(stack, key)
                message = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "read-time",
                            "type": "function",
                            "function": {"name": "current_time", "arguments": "{}"},
                        }
                    ],
                }
            else:
                message = {"role": "assistant", "content": "done"}
        return {
            "choices": [
                {"finish_reason": "tool_calls" if "tool_calls" in message else "stop", "message": message}
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }

    return app, bodies, primary


async def delete_fact(stack, key):
    from muad_agent_runtime.application.memory_service import MemoryService
    from muad_common import SharedSettings
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        await MemoryService(async_sessionmaker(engine, expire_on_commit=False)).execute(
            context(tenant=stack.tenant_id, user=stack.platform_user_id),
            command(commands().MemoryMutation(action="DELETE", memory_key=key, expected_version=1)),
        )
    finally:
        await engine.dispose()


def configure(stack, endpoint, layer):
    async def update(factory):
        async with factory.begin() as session:
            credential = await _member_credential(session, stack.model_id)
            agent = await session.get(AgentDefinition, stack.agent_id)
            original = credential.base_url, agent.runtime_config
            credential.base_url = endpoint + "/v1"
            compaction = {
                "snip": {
                    "enabled": layer == "snip",
                    "max_groups": 3,
                    "keep_head_groups": 1,
                    "keep_tail_groups": 1,
                },
                "micro": {"enabled": layer == "micro", "keep_recent_tool_groups": 1},
                "summary": {
                    "enabled": layer == "summary",
                    "threshold_bytes": 0,
                    "model_ref": str(stack.model_id) if layer == "summary" else None,
                },
            }
            agent.runtime_config = {"budget": {"compaction": compaction}}
            return original

    return run_db(update)


def restore(stack, original):
    async def update(factory):
        async with factory.begin() as session:
            credential = await _member_credential(session, stack.model_id)
            agent = await session.get(AgentDefinition, stack.agent_id)
            credential.base_url, agent.runtime_config = original

    run_db(update)


def seed_history(stack, conversation, key):
    from muad_agent_runtime.application.memory_service import MemoryService

    async def seed(factory):
        await MemoryService(factory).execute(
            context(tenant=stack.tenant_id, user=stack.platform_user_id),
            command(upsert(key, "记忆前缀必须保留")),
        )
        async with factory.begin() as session:
            old_run = RunRecord(
                id=uuid4(),
                tenant_id=stack.tenant_id,
                conversation_id=conversation,
                user_id=stack.platform_user_id,
                agent_id=stack.agent_id,
                status="COMPLETED",
                input_text="历史上下文",
                trace_id=uuid4().hex,
            )
            session.add(old_run)
            await session.flush()
            writer = EventWriter(session)
            await writer.append(
                tenant_id=stack.tenant_id,
                conversation_id=conversation,
                run_id=old_run.id,
                event_type="CONTEXT_SUMMARY",
                payload={"covers_up_to_seq": 0, "summary": SUMMARY},
            )
            for index in range(5):
                await _seed_round(session, stack, conversation, old_run.id, index)

    run_db(seed)


async def _seed_round(session, stack, conversation, run_id, index):
    writer = EventWriter(session)
    await writer.append(
        tenant_id=stack.tenant_id,
        conversation_id=conversation,
        run_id=run_id,
        event_type="USER_MESSAGE",
        payload={"text": f"历史诉求{index}"},
    )
    await writer.append(
        tenant_id=stack.tenant_id,
        conversation_id=conversation,
        run_id=run_id,
        event_type="ASSISTANT_TURN",
        payload={
            "text": "",
            "tool_calls": [{"id": f"old-{index}", "name": "read_attachment", "arguments": {}}],
        },
    )
    artifact = _artifact(stack, conversation, run_id)
    session.add(artifact)
    await session.flush()
    await writer.append(
        tenant_id=stack.tenant_id,
        conversation_id=conversation,
        run_id=run_id,
        artifact_id=artifact.id,
        event_type="TOOL_CALL",
        payload={
            "tool_call_id": f"old-{index}",
            "tool_name": "read_attachment",
            "preview": "真实外置工具正文",
        },
    )


def _artifact(stack, conversation, run_id):
    import hashlib

    content = ("旧工具正文" * 80).encode("utf-8")
    key = f"e01/{uuid4().hex}.txt"
    path = stack.artifact_root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return Artifact(
        tenant_id=stack.tenant_id,
        conversation_id=conversation,
        run_id=run_id,
        artifact_type="TOOL_RESULT",
        storage_key=key,
        media_type="text/plain",
        size=len(content),
        checksum="sha256:" + hashlib.sha256(content).hexdigest(),
        preview_text=content.decode("utf-8"),
    )


def fired_layers(stack, conversation):
    async def read(factory):
        async with factory() as session:
            rows = await session.scalars(
                select(CanonicalEvent).where(
                    CanonicalEvent.tenant_id == stack.tenant_id,
                    CanonicalEvent.conversation_id == conversation,
                    CanonicalEvent.event_type == "CONTEXT_COMPACTED",
                )
            )
            return {
                key for row in rows for key, value in row.payload_json["layers"].items() if value["fired"]
            }

    return run_db(read)
