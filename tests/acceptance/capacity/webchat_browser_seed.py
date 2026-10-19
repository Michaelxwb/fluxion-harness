"""Webchat 浏览器验收种子原语（TASK-114；真实 ORM/真实 PG，供 e2e/tests/webchat 调用）。

**为什么需要种子**：`playwright.webchat.config.ts` 起的栈里，浏览器读路径（入口代理 → Gateway →
Runtime → PG → 真实 Chrome）全是产品实现；但有几类**读取侧**验收需要真实行数据，而生产写入路径
在当前探针能力下无法构造：

- **>200 条保留历史**（S-06 / S-10）：真实 Run 每轮只落 2 条可见消息，200+ 条要靠上百次模型调用，
  且与断言无关；这里按 canonical 协议写真实行，读取仍走真实分页链路。
- **内部事件 canary**（E-13）：工具/推理/SYSTEM/未知事件由真实模型工具回合产生，探针不支持
  tool_call；这里按 canonical 协议写真实行，公开投影仍由产品过滤。
- **待确认 prompt**（S-06）：记忆确认需要模型发起 `remember` 工具调用；这里**调用产品自己的**
  `MemoryConfirmationService` + `suspend_memory_confirmation`（不是手拼 proposal_json），
  中断/事件/通知仍由产品代码写。
- **暂存过期 / 制品失效**（E-14 / E-104）：TTL 最短 60 秒，等待不改变被测语义；这里把真实行的
  `expires_at`/`is_deleted` 改到目标状态，读取与拒绝仍走产品裁决。

**边界**：只写真实 PG 行（或调用真实服务函数），不 mock 任何 HTTP/业务响应；不写共享 dev 库
（`E2E_ACC_DATABASE_URL` 是本轮独占空库）。所有输出都是 JSON（一行），供 TS 侧解析。

用法（TS 侧经 `e2e/support/webchat-seed.ts` 调用，不直接拼 shell）：

    uv run python -m tests.acceptance.capacity.webchat_browser_seed history \
        --tenant default --user <uid> --agent <aid> --entry <eid> --messages 205 --canaries
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_agent_runtime.application.async_tools.continuation_service import ExecutionIdentity
from muad_agent_runtime.application.channel_notifications import (
    KIND_RUN_COMPLETED,
    run_terminal_delivery_key,
    write_run_notification,
)
from muad_agent_runtime.application.checkpoint import suspend_memory_confirmation
from muad_agent_runtime.application.memory_confirmation import MemoryConfirmationService
from muad_agent_runtime.application.run_events import EventWriter
from muad_agent_runtime.infrastructure.models.async_tools import ToolOperation, ToolResultInbox
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    ChannelUpload,
    Conversation,
    RunInterrupt,
    RunRecord,
    RunSubmission,
)
from muad_contracts.memory import MemoryConfirmationProposal, RememberMemoryInput, memory_hash
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

#: 内部事件 canary：写入 canonical 行后必须被公开投影过滤掉（E-13）。
CANARY_TOOL_CALL = "canary-tool-call-1c1c1c1c"
CANARY_MODEL_REQUEST = "canary-model-request-2d2d2d2d"
CANARY_SYSTEM = "canary-system-3e3e3e3e"
CANARY_REASONING = "canary-reasoning-4f4f4f4f"
CANARY_UNKNOWN = "canary-unknown-event-5a5a5a5a"

#: 内部事件的 canonical 形状（与产品写入方同词表；公开投影只认白名单）。
INTERNAL_CANARY_EVENTS: tuple[tuple[str, str | None, dict[str, Any]], ...] = (
    ("TOOL_CALL", None, {"tool_call_id": "canary-1", "arguments": {"q": CANARY_TOOL_CALL}}),
    ("MODEL_REQUEST", None, {"messages": [{"role": "user", "content": CANARY_MODEL_REQUEST}]}),
    ("SYSTEM_MESSAGE", None, {"text": CANARY_SYSTEM}),
    ("ASSISTANT_REASONING", "message.reasoning", {"text": CANARY_REASONING}),
    ("INTERNAL_UNKNOWN", "internal.unknown", {"text": CANARY_UNKNOWN}),
)

#: 公开投影必须原样展示的增量文本（E-13/E-107）。
PUBLIC_DELTAS: tuple[str, ...] = ("可见", "增量", "正文")


def _database_url() -> str:
    value = os.environ.get("E2E_ACC_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not value:
        raise SystemExit("缺少 E2E_ACC_DATABASE_URL/DATABASE_URL：本工具只对独占空库运行")
    return value


def _print(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


async def _append_message(
    session: Any, *, tenant_id: str, conversation_id: uuid.UUID, run_id: uuid.UUID, role: str, text: str
) -> int:
    event_type = "USER_MESSAGE" if role == "USER" else "ASSISTANT_MESSAGE"
    return await EventWriter(session).append(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        run_id=run_id,
        event_type=event_type,
        payload={"text": text},
    )


async def _new_conversation(
    session: Any,
    *,
    tenant_id: str,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    entry_id: uuid.UUID,
    title: str,
    last_run_id: uuid.UUID | None = None,
) -> Conversation:
    conversation = Conversation(
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=agent_id,
        title=title,
        status="ACTIVE",
        last_seq=0,
        channel_account_id=entry_id,
        external_thread_id=str(user_id),
        last_run_id=last_run_id,
        last_message_at=datetime.now(UTC),
    )
    session.add(conversation)
    await session.flush()
    return conversation


async def seed_history(args: argparse.Namespace) -> dict[str, Any]:
    """真实 canonical 历史：`--messages` 条可见消息（交替角色）+ 可选内部 canary 事件。"""
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory.begin() as session:
            conversation = await _new_conversation(
                session,
                tenant_id=args.tenant,
                user_id=uuid.UUID(args.user),
                agent_id=uuid.UUID(args.agent),
                entry_id=uuid.UUID(args.entry),
                title=args.title,
            )
            run_id = uuid.uuid4()
            seq = 0
            for index in range(args.messages):
                role = "USER" if index % 2 == 0 else "ASSISTANT"
                text = f"历史消息 {index + 1}（{role}）"
                await _append_message(
                    session,
                    tenant_id=args.tenant,
                    conversation_id=conversation.id,
                    run_id=run_id,
                    role=role,
                    text=text,
                )
                seq = conversation.last_seq
                if args.canaries and index == args.messages // 2:
                    for event_type, stream_type, payload in INTERNAL_CANARY_EVENTS:
                        await EventWriter(session).append(
                            tenant_id=args.tenant,
                            conversation_id=conversation.id,
                            run_id=run_id,
                            event_type=event_type,
                            stream_type=stream_type,
                            payload=payload,
                        )
                        seq = conversation.last_seq
            conversation.last_run_id = run_id
            await session.flush()
            return {
                "conversation_id": str(conversation.id),
                "run_id": str(run_id),
                "messages": args.messages,
                "last_seq": seq,
            }
    finally:
        await engine.dispose()


async def seed_streaming_run(args: argparse.Namespace) -> dict[str, Any]:
    """一条**进行中**的真实 Run：公开增量 + 过滤跳号的内部 canary，终态由 `complete-run` 追加。

    `--start-seq` 模拟「保留策略已淘汰早期行」的会话（最早可用行 > 1）：客户端用 `after_seq=0`
    订阅时服务端必须显式报 `CURSOR_EXPIRED`，而不是静默从最早行补发。
    """
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory.begin() as session:
            conversation = await _new_conversation(
                session,
                tenant_id=args.tenant,
                user_id=uuid.UUID(args.user),
                agent_id=uuid.UUID(args.agent),
                entry_id=uuid.UUID(args.entry),
                title=args.title,
            )
            if args.start_seq > 1:
                conversation.last_seq = args.start_seq - 1
            run_id = uuid.uuid4()
            run = RunRecord(
                id=run_id,
                tenant_id=args.tenant,
                conversation_id=conversation.id,
                user_id=uuid.UUID(args.user),
                agent_id=uuid.UUID(args.agent),
                status="RUNNING",
                input_text="流式验收",
                trace_id=uuid.uuid4().hex,
                start_time=datetime.now(UTC),
                deadline_at=datetime.now(UTC) + timedelta(minutes=30),
                execution_epoch=1,
                lease_owner="webchat-browser-seed",
                lease_until=datetime.now(UTC) + timedelta(minutes=30),
            )
            session.add(run)
            await session.flush()
            conversation.last_run_id = run_id
            for delta in PUBLIC_DELTAS:
                await EventWriter(session).append(
                    tenant_id=args.tenant,
                    conversation_id=conversation.id,
                    run_id=run_id,
                    event_type="MODEL_DELTA",
                    stream_type="message.delta",
                    payload={"delta": delta, "reply_generation": 0},
                )
            for event_type, stream_type, payload in INTERNAL_CANARY_EVENTS:
                await EventWriter(session).append(
                    tenant_id=args.tenant,
                    conversation_id=conversation.id,
                    run_id=run_id,
                    event_type=event_type,
                    stream_type=stream_type,
                    payload=payload,
                )
            for delta in PUBLIC_DELTAS:
                await EventWriter(session).append(
                    tenant_id=args.tenant,
                    conversation_id=conversation.id,
                    run_id=run_id,
                    event_type="MODEL_DELTA",
                    stream_type="message.delta",
                    payload={"delta": delta, "reply_generation": 0},
                )
            await session.flush()
            return {
                "conversation_id": str(conversation.id),
                "run_id": str(run_id),
                "last_seq": conversation.last_seq,
                "text": "".join(PUBLIC_DELTAS + PUBLIC_DELTAS),
            }
    finally:
        await engine.dispose()


async def complete_run(args: argparse.Namespace) -> dict[str, Any]:
    """把种子 Run 收尾：终态事件 + 助手正文 + 终态通知**同一事务**（与产品 `_finalize_run` 同接缝）。

    助手正文按该 Run 已持久化的公开增量拼接（真实 canonical 读取），不另造一份文案。
    """
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory.begin() as session:
            run = await session.get(RunRecord, uuid.UUID(args.run))
            if run is None:
                raise SystemExit(f"run 不存在：{args.run}")
            deltas = list(
                await session.scalars(
                    sa.select(CanonicalEvent.payload_json)
                    .where(
                        CanonicalEvent.tenant_id == run.tenant_id,
                        CanonicalEvent.conversation_id == run.conversation_id,
                        CanonicalEvent.run_id == run.id,
                        CanonicalEvent.stream_type == "message.delta",
                        CanonicalEvent.is_deleted.is_(False),
                    )
                    .order_by(CanonicalEvent.seq)
                )
            )
            text = "".join(
                payload.get("delta", "") for payload in deltas if isinstance(payload, dict)
            )
            await EventWriter(session).append(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                event_type="ASSISTANT_MESSAGE",
                payload={"text": text},
            )
            await EventWriter(session).append(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                event_type="RUN_COMPLETED",
                stream_type="run.completed",
                payload={"status": "COMPLETED"},
            )
            run.status, run.end_time = "COMPLETED", datetime.now(UTC)
            receipt = await write_run_notification(
                session,
                run=run,
                kind=KIND_RUN_COMPLETED,
                delivery_key=run_terminal_delivery_key(run.id),
            )
            return {
                "run_id": str(run.id),
                "conversation_id": str(run.conversation_id),
                "text": text,
                "notification_id": None if receipt is None else str(receipt.id),
                "notification_created": None if receipt is None else receipt.created,
            }
    finally:
        await engine.dispose()



async def _seed_snapshot(
    session: Any,
    *,
    tenant_id: str,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
) -> Any:
    """为种子 Run 写一条**真实形状**的冻结快照（`RuntimeSnapshot` 的 NOT NULL 字段齐全）。

    待确认挂起（`_write_checkpoint`）要求 Run 有冻结快照；这里按 control 侧真实定义拼出
    `agent_json`/`model_json`（模型根地址带 `/v1`，与 provider 的 `/chat/completions` 拼接口径一致），
    `policy_json` 用产品自己的 `snapshot_policy()`（完整冻结口径：resume/续跑读回时要求
    memory 分组每个 schema 叶齐全），但该 Run 不会执行——快照只作为"挂起需要冻结事实"的真实行。
    """
    from muad_agent_runtime.application.run_service import snapshot_policy
    from muad_agent_runtime.infrastructure.models.runtime import RuntimeSnapshot
    from muad_console_platform.infrastructure.models.control import (
        AgentDefinition,
        ModelCredential,
        ModelDefinition,
        ModelDeployment,
    )

    agent = await session.get(AgentDefinition, agent_id)
    if agent is None:
        raise SystemExit(f"agent 不存在：{agent_id}")
    model = await session.get(ModelDefinition, agent.model_id)
    if model is None:
        raise SystemExit(f"model 不存在：{agent.model_id}")
    deployment = await session.scalar(
        sa.select(ModelDeployment).where(
            ModelDeployment.tenant_id == tenant_id,
            ModelDeployment.model_id == model.id,
            ModelDeployment.is_deleted.is_(False),
        )
    )
    if deployment is None:
        raise SystemExit(f"model 没有成员：{model.id}")
    credential = await session.get(ModelCredential, deployment.credential_id)
    if credential is None:
        raise SystemExit(f"credential 不存在：{deployment.credential_id}")
    agent_json = {
        "id": str(agent.id),
        "key": agent.key,
        "revision": int(agent.revision),
        "instructions": agent.instructions,
        "runtime_config": agent.runtime_config or {},
    }
    model_json = {
        "id": str(model.id),
        "revision": int(model.revision),
        "protocol": "OPENAI",
        "model_id": model.model_id,
        "base_url": credential.base_url,
        "api_key": credential.api_key,
        "params": model.params_json or {},
    }
    snapshot = RuntimeSnapshot(
        tenant_id=tenant_id,
        run_id=run_id,
        schema_version=1,
        agent_revision=int(agent.revision),
        model_revision=int(model.revision),
        agent_json=agent_json,
        model_json=model_json,
        skill_catalog_json=[],
        mcp_catalog_json=[],
        policy_json=snapshot_policy(),
        prompt_template_version="1",
        content_hash=hashlib.sha256(
            json.dumps({"agent": agent_json, "model": model_json}, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    )
    del user_id, conversation_id
    session.add(snapshot)
    await session.flush()
    return snapshot


async def seed_pending_confirmation(args: argparse.Namespace) -> dict[str, Any]:
    """真实待确认：产品 `MemoryConfirmationService` 提议 + `suspend_memory_confirmation` 落库。"""
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        submission_id = uuid.uuid4()
        user_id = uuid.UUID(args.user)
        async with factory.begin() as session:
            conversation = await _new_conversation(
                session,
                tenant_id=args.tenant,
                user_id=user_id,
                agent_id=uuid.UUID(args.agent),
                entry_id=uuid.UUID(args.entry),
                title=args.title,
            )
            run_id = uuid.uuid4()
            snapshot = await _seed_snapshot(
                session,
                tenant_id=args.tenant,
                user_id=user_id,
                agent_id=uuid.UUID(args.agent),
                conversation_id=conversation.id,
                run_id=run_id,
            )
            run = RunRecord(
                id=run_id,
                tenant_id=args.tenant,
                conversation_id=conversation.id,
                user_id=user_id,
                agent_id=uuid.UUID(args.agent),
                status="RUNNING",
                input_text="请记住我的偏好",
                trace_id=uuid.uuid4().hex,
                start_time=datetime.now(UTC),
                deadline_at=datetime.now(UTC) + timedelta(minutes=30),
                execution_epoch=1,
                lease_owner="webchat-browser-seed",
                lease_until=datetime.now(UTC) + timedelta(minutes=30),
                snapshot_id=snapshot.id,
            )
            session.add(run)
            await session.flush()
            session.add(
                RunSubmission(
                    id=submission_id,
                    tenant_id=args.tenant,
                    actor_user_id=user_id,
                    run_id=run_id,
                    conversation_id=conversation.id,
                    idempotency_key=uuid.uuid4().hex,
                    endpoint="CREATE",
                    request_fingerprint="webchat-browser-seed",
                )
            )
            await session.flush()
            await EventWriter(session).append(
                tenant_id=args.tenant,
                conversation_id=conversation.id,
                run_id=run_id,
                submission_id=submission_id,
                event_type="USER_MESSAGE",
                payload={"text": args.quote, "source_scope_revision": 0},
            )
            conversation.last_run_id = run_id
            await session.flush()
        identity = ExecutionIdentity(args.tenant, run_id, "webchat-browser-seed", 1)
        service = MemoryConfirmationService(factory, identity, submission_id=submission_id)
        candidate = RememberMemoryInput(
            memory_key=args.memory_key,
            category="PREFERENCE",
            value=args.value,
            source_quote=args.quote,
        )
        await service.propose(candidate, call_id="webchat-browser-seed-remember")
        request = service._pending
        if request is None:
            raise SystemExit("记忆确认提议未挂起：产品服务未返回 MemorySuspendRequest")
        await suspend_memory_confirmation(factory, identity, request, RunnerCheckpoint())
        async with factory() as session:
            interrupt = await session.scalar(
                sa.select(RunInterrupt).where(
                    RunInterrupt.tenant_id == args.tenant,
                    RunInterrupt.run_id == run_id,
                    RunInterrupt.interrupt_type == "MEMORY_CONFIRMATION",
                )
            )
            return {
                "conversation_id": str(conversation.id),
                "run_id": str(run_id),
                "prompt": None if interrupt is None else interrupt.prompt_text,
                "interrupt_id": None if interrupt is None else str(interrupt.id),
            }
    finally:
        await engine.dispose()


async def expire_confirmation(args: argparse.Namespace) -> dict[str, Any]:
    """把待确认中断的有效期改到过去（E-104 的「确认过期」本地化反馈）。"""
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory.begin() as session:
            interrupt = await session.scalar(
                sa.select(RunInterrupt).where(
                    RunInterrupt.tenant_id == args.tenant,
                    RunInterrupt.run_id == uuid.UUID(args.run),
                    RunInterrupt.interrupt_type == "MEMORY_CONFIRMATION",
                    RunInterrupt.status == "WAITING",
                    RunInterrupt.is_deleted.is_(False),
                )
            )
            if interrupt is None:
                raise SystemExit(f"待确认中断不存在：{args.run}")
            # 一致性改写：待确认的 proposal_hash 冻在 canonical 证据行里（读取路径逐项复核），
            # 所以 expires_at 前移必须把 interrupt.proposal_json、证据行的 proposal_hash 与
            # prompt 一起改写——不能只改一半（那会被 `validated_proposal` 判为数据异常）。
            expired = datetime.now(UTC) - timedelta(minutes=5)
            proposal = MemoryConfirmationProposal.model_validate(interrupt.proposal_json)
            updated = proposal.model_copy(update={"expires_at": expired})
            interrupt.expires_at = expired
            interrupt.proposal_json = updated.model_dump(mode="json")
            shown = await session.scalar(
                sa.select(CanonicalEvent).where(
                    CanonicalEvent.tenant_id == args.tenant,
                    CanonicalEvent.run_id == uuid.UUID(args.run),
                    CanonicalEvent.event_type == "MEMORY_CONFIRMATION_REQUIRED",
                    CanonicalEvent.is_deleted.is_(False),
                )
            )
            if shown is None:
                raise SystemExit(f"待确认证据事件不存在：{args.run}")
            shown.payload_json = {
                **shown.payload_json,
                "prompt": interrupt.prompt_text,
                "expires_at": expired.isoformat(),
                "proposal_hash": memory_hash(updated.model_dump(mode="json")),
            }
            return {"interrupt_id": str(interrupt.id), "expires_at": expired.isoformat()}
    finally:
        await engine.dispose()


async def expire_upload(args: argparse.Namespace) -> dict[str, Any]:
    """把暂存上传的 TTL 改到过去（E-14 的「暂存过期」；TTL 下限 60s，等待不改语义）。

    `channel_upload` 有 CHECK `expires_at > create_time`（数据库事实），所以 create_time 与
    expires_at **一起**前移：过期事实成立、约束不被绕过。
    """
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory.begin() as session:
            row = await session.get(ChannelUpload, uuid.UUID(args.upload))
            if row is None:
                raise SystemExit(f"暂存上传不存在：{args.upload}")
            now = datetime.now(UTC)
            row.create_time = now - timedelta(minutes=10)
            row.expires_at = now - timedelta(minutes=5)
            return {"upload_id": str(row.id), "expires_at": row.expires_at.isoformat()}
    finally:
        await engine.dispose()


async def delete_artifact(args: argparse.Namespace) -> dict[str, Any]:
    """软删制品行（E-104 的「附件失效」；字节仍在共享卷，拒绝由产品裁决）。"""
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        from muad_agent_runtime.infrastructure.models.runtime import Artifact

        async with factory.begin() as session:
            row = await session.get(Artifact, uuid.UUID(args.artifact))
            if row is None:
                raise SystemExit(f"制品不存在：{args.artifact}")
            row.is_deleted = True
            return {"artifact_id": str(row.id), "is_deleted": True}
    finally:
        await engine.dispose()


async def renotify(args: argparse.Namespace) -> dict[str, Any]:
    """按同一 `delivery_key` 重投终态通知（E-13/S-113 的「重放不重复累计未读」）。"""
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory.begin() as session:
            run = await session.get(RunRecord, uuid.UUID(args.run))
            if run is None:
                raise SystemExit(f"run 不存在：{args.run}")
            receipt = await write_run_notification(
                session,
                run=run,
                kind=KIND_RUN_COMPLETED,
                delivery_key=run_terminal_delivery_key(run.id),
            )
            return {
                "notification_id": None if receipt is None else str(receipt.id),
                "created": None if receipt is None else receipt.created,
                "seq": None if receipt is None else receipt.seq,
            }
    finally:
        await engine.dispose()


async def seed_task_result(args: argparse.Namespace) -> dict[str, Any]:
    """外部任务结果入箱（E-109）：真实 Task/操作/受理结果行 + **真实产品服务**写 TASK_RESULT 通知。

    Task 结果在页面上的可见形态就是收件箱通知（kind=TASK_RESULT，`summary` 由读取层投影）；
    「待核实」是 Task 行的终态事实（`status=FAILED` + `error_code=TASK_RESULT_UNVERIFIED` +
    `attempt=1`），本原语把两件事都写成真实行/真实服务回执：通知经
    `ChannelNotificationQueryService.register_task_result`（与 Worker 投递同一入口）核验
    operation/受理结果/Run/入口归属后落库，不是手拼通知行。
    """
    from muad_agent_runtime.application.channel_notification_queries import (
        ChannelNotificationQueryService,
    )
    from muad_agent_worker.infrastructure.models.task import TaskExecution
    from muad_contracts.webchat_events import WebchatTaskResultRegistration

    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    task_id = uuid.uuid4()
    try:
        async with factory.begin() as session:
            conversation = await _new_conversation(
                session,
                tenant_id=args.tenant,
                user_id=uuid.UUID(args.user),
                agent_id=uuid.UUID(args.agent),
                entry_id=uuid.UUID(args.entry),
                title=args.title,
            )
            run_id = uuid.uuid4()
            now = datetime.now(UTC)
            session.add(
                RunRecord(
                    id=run_id,
                    tenant_id=args.tenant,
                    conversation_id=conversation.id,
                    user_id=uuid.UUID(args.user),
                    agent_id=uuid.UUID(args.agent),
                    status="COMPLETED",
                    input_text="外部任务结果验收",
                    trace_id=uuid.uuid4().hex,
                    start_time=now,
                    end_time=now,
                    deadline_at=now + timedelta(minutes=30),
                    execution_epoch=1,
                )
            )
            await session.flush()
            conversation.last_run_id = run_id
            receipt_event_id = uuid.uuid4()
            receipt_seq = await EventWriter(session).append(
                tenant_id=args.tenant,
                conversation_id=conversation.id,
                run_id=run_id,
                event_type="TOOL_RESULT_RECEIVED",
                payload={"task_id": str(task_id), "status": "UNVERIFIED"},
                event_id=receipt_event_id,
            )
            operation_id = uuid.uuid4()
            session.add(
                ToolOperation(
                    id=operation_id,
                    tenant_id=args.tenant,
                    run_id=run_id,
                    actor_user_id=uuid.UUID(args.user),
                    source_tool_call_id=f"seed-task-call-{task_id.hex[:8]}",
                    completion_mode="DETACH",
                    status="RESULT_RECEIVED",
                    task_id=task_id,
                    task_snapshot_hash=uuid.uuid4().hex,
                    submission_json={"skill_key": "seed-external-write"},
                    input_hash=uuid.uuid4().hex,
                )
            )
            await session.flush()
            session.add(
                ToolResultInbox(
                    tenant_id=args.tenant,
                    event_id=uuid.uuid4(),
                    run_id=run_id,
                    operation_id=operation_id,
                    task_id=task_id,
                    task_event_seq=1,
                    payload_hash=uuid.uuid4().hex,
                    payload_json={"status": "UNVERIFIED", "error_code": "TASK_RESULT_UNVERIFIED"},
                    late=True,
                    receipt_event_id=receipt_event_id,
                    receipt_seq=receipt_seq,
                )
            )
            session.add(
                TaskExecution(
                    id=task_id,
                    tenant_id=args.tenant,
                    source_run_id=run_id,
                    source_operation_id=operation_id,
                    source_tool_call_id=f"seed-task-call-{task_id.hex[:8]}",
                    completion_mode="DETACH",
                    agent_id=uuid.UUID(args.agent),
                    actor_user_id=uuid.UUID(args.user),
                    intent_key=f"seed-task-{task_id.hex[:8]}",
                    skill_id=uuid.uuid4(),
                    skill_artifact_id=uuid.uuid4(),
                    trigger_type="TOOL",
                    execution_mode="ASYNC",
                    task_type="SKILL",
                    status="FAILED",
                    input_json={"payload": {}},
                    result_json={"status": "UNVERIFIED", "error_code": "TASK_RESULT_UNVERIFIED"},
                    error_code="TASK_RESULT_UNVERIFIED",
                    error_message="外部写结果待核实：响应丢失，不自动重做",
                    execution_snapshot_json={"schema_version": 1},
                    snapshot_hash=uuid.uuid4().hex,
                    idempotency_key=f"seed-task-{task_id.hex[:8]}",
                    attempt=1,
                    delivery_key=f"task:{task_id}:final",
                    started_at=now,
                    finished_at=now,
                )
            )
            await session.flush()
        async with factory() as session, session.begin():
            receipt = await ChannelNotificationQueryService(session).register_task_result(
                tenant_id=args.tenant,
                registration=WebchatTaskResultRegistration(
                    task_id=task_id,
                    run_id=run_id,
                    user_id=uuid.UUID(args.user),
                    artifact_ids=[],
                ),
            )
        return {
            "conversation_id": str(conversation.id),
            "run_id": str(run_id),
            "task_id": str(task_id),
            "notification_id": str(receipt.notification_id),
            "seq": int(receipt.seq),
        }
    finally:
        await engine.dispose()


async def append_oversized_delta(args: argparse.Namespace) -> dict[str, Any]:
    """追加一条超过单帧预算的公开增量（E-18 的「超大事件」：网关必须显式关闭订阅）。"""
    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory.begin() as session:
            run = await session.get(RunRecord, uuid.UUID(args.run))
            if run is None:
                raise SystemExit(f"run 不存在：{args.run}")
            seq = await EventWriter(session).append(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                event_type="MODEL_DELTA",
                stream_type="message.delta",
                payload={"delta": "x" * args.bytes, "reply_generation": 0},
            )
            return {"run_id": str(run.id), "seq": seq, "bytes": args.bytes}
    finally:
        await engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Webchat 浏览器验收种子原语")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--tenant", required=True)
        p.add_argument("--user", required=True)
        p.add_argument("--agent", required=True)
        p.add_argument("--entry", required=True)
        p.add_argument("--title", default="验收会话")

    history = sub.add_parser("history")
    common(history)
    history.add_argument("--messages", type=int, required=True)
    history.add_argument("--canaries", action="store_true")

    streaming = sub.add_parser("streaming-run")
    common(streaming)
    streaming.add_argument("--start-seq", type=int, default=1)

    confirm = sub.add_parser("pending-confirmation")
    common(confirm)
    confirm.add_argument("--quote", default="我的偏好是蓝色")
    confirm.add_argument("--memory-key", default="color")
    confirm.add_argument("--value", default="blue")

    complete = sub.add_parser("complete-run")
    complete.add_argument("--run", required=True)

    expire = sub.add_parser("expire-confirmation")
    expire.add_argument("--tenant", required=True)
    expire.add_argument("--run", required=True)

    upload = sub.add_parser("expire-upload")
    upload.add_argument("--upload", required=True)

    artifact = sub.add_parser("delete-artifact")
    artifact.add_argument("--artifact", required=True)

    notify = sub.add_parser("renotify")
    notify.add_argument("--run", required=True)

    oversized = sub.add_parser("append-oversized-delta")
    oversized.add_argument("--run", required=True)
    oversized.add_argument("--bytes", type=int, required=True)

    task_result = sub.add_parser("task-result")
    common(task_result)
    return parser


_HANDLERS = {
    "history": seed_history,
    "streaming-run": seed_streaming_run,
    "pending-confirmation": seed_pending_confirmation,
    "complete-run": complete_run,
    "expire-confirmation": expire_confirmation,
    "expire-upload": expire_upload,
    "delete-artifact": delete_artifact,
    "renotify": renotify,
    "append-oversized-delta": append_oversized_delta,
    "task-result": seed_task_result,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = asyncio.run(_HANDLERS[args.command](args))
    _print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
