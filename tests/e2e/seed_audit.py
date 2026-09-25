"""11-audit-observability 验收环境种子：真实 control/runtime 数据（四类审计 + 导出任务）。

- 租户 `audit-acceptance-<uuid>`：本模块独占，可按前缀发现、可幂等清理；
- 写入一律走生产实现（api-kit `write_config_audit`、Runtime `RuntimeAuditWriter`、
  Console `AuditExportService` + artifact store），不手写 INSERT，种子形态与线上一致；
- 四类审计（CONFIG/TOOL/EGRESS/MODEL）由同一 `TRACE_ID` 串联：TOOL/EGRESS/MODEL 归属
  同一 `runtime.run_record`（trace 来自 run），CONFIG 经 trace context 落自身 `trace_id`。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

TENANT = f"audit-acceptance-{uuid.uuid4().hex[:8]}"
TRACE_ID = f"audit-trace-{uuid.uuid4().hex[:12]}"

ACCOUNT_USERNAME = "audit-acceptance-admin"
# 本地验收账号口令：仅用于本模块真实登录（argon2 哈希落库），不是任何环境的凭据
ACCOUNT_PASSWORD = "audit-acceptance-password"
USER_CODE = "audit-acceptance-user"
AGENT_KEY = "audit-acceptance-agent"
MODEL_KEY = "audit-acceptance-model"
MODEL_NAME = "gpt-4o-mini"
MODEL_API_KEY = "acceptance-probe-key"
CONFIG_RESOURCE_TYPE = "AGENT"
CONFIG_ACTION = "UPDATE_AGENT"
CONFIG_ACTION_BEFORE = {"name": "Audit Acceptance Agent", "enabled": True}
CONFIG_ACTION_AFTER = {"name": "Audit Acceptance Agent", "enabled": False}
TOOL_NAME = "acceptance_policy_check"
TOOL_KIND = "SKILL"
TOOL_STATUS = "SUCCEEDED"
EGRESS_TARGET_TYPE = "HTTP"
EGRESS_TARGET = "https://egress.audit-acceptance.invalid/report"
EGRESS_OPERATION = "REPORT_PUSH"
EGRESS_RESULT_STATUS = "OK"
MODEL_STATUS = "SUCCEEDED"
EXPORT_FORMAT = "JSON"
EXPORT_IDEMPOTENCY_KEY = "audit-acceptance-export-1"
# 一次种子落库的审计行数（四类各一条）：用例断言行数与导出产物行数
AUDIT_ROW_COUNT = 4

SessionFactory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class _Account:
    """Console 账号 + 平台用户（Agent/模型尚未建）。"""

    account_id: uuid.UUID
    account_username: str
    user_id: uuid.UUID


@dataclass(frozen=True)
class _Identity:
    account_id: uuid.UUID
    account_username: str
    user_id: uuid.UUID
    model_id: uuid.UUID
    agent_id: uuid.UUID


@dataclass(frozen=True)
class AuditSeedContext:
    """种子的主体（同一 trace 的 run/agent/user/账号）。"""

    tenant_id: str
    trace_id: str
    account_id: uuid.UUID
    account_username: str
    user_id: uuid.UUID
    agent_id: uuid.UUID
    model_id: uuid.UUID
    conversation_id: uuid.UUID
    run_id: uuid.UUID


@dataclass(frozen=True)
class AuditRowIds:
    """四类审计事实行 id（`CONFIG`/`TOOL`/`EGRESS`/`MODEL`）。"""

    config: uuid.UUID
    tool: uuid.UUID
    egress: uuid.UUID
    model: uuid.UUID


@dataclass(frozen=True)
class ExportSeed:
    """导出任务种子：任务号、产物引用、行数与幂等键。"""

    export_id: uuid.UUID
    artifact_ref: str
    row_count: int
    idempotency_key: str


@dataclass(frozen=True)
class AuditSeed:
    context: AuditSeedContext
    rows: AuditRowIds
    export: ExportSeed
    account_password: str


async def _create_account(factory: SessionFactory) -> _Account:
    """Console 账号 + 平台用户：审计 actor（`console_account`）与其业务身份。"""
    from muad_console_platform.application.auth_service import hash_password
    from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount
    from muad_console_platform.infrastructure.models.control import PlatformUser

    async with factory() as session:
        account = ConsoleAccount(
            tenant_id=TENANT,
            username=ACCOUNT_USERNAME,
            display_name="Audit Acceptance Admin",
            password_hash=hash_password(ACCOUNT_PASSWORD),
            role=ROLE_ADMIN,
        )
        session.add(account)
        await session.flush()
        user = PlatformUser(
            tenant_id=TENANT, user_code=USER_CODE, display_name="Audit Acceptance User"
        )
        session.add(user)
        await session.flush()
        await session.commit()
        return _Account(
            account_id=account.id, account_username=account.username, user_id=user.id
        )


async def _create_agent(
    factory: SessionFactory, account: _Account, model_base_url: str
) -> _Identity:
    """模型 + Agent + 用户授权：运行审计的 Agent 权威行（名称由聚合投影 JOIN 补齐）。"""
    from muad_console_platform.infrastructure.models.control import (
        AgentAccessGrant,
        AgentDefinition,
        ModelDefinition,
    )

    async with factory() as session:
        model = ModelDefinition(
            tenant_id=TENANT,
            key=MODEL_KEY,
            name="Audit Acceptance Model",
            model_id=MODEL_NAME,
            base_url=model_base_url,
            api_key=MODEL_API_KEY,
        )
        session.add(model)
        await session.flush()
        agent = AgentDefinition(
            tenant_id=TENANT,
            key=AGENT_KEY,
            name="Audit Acceptance Agent",
            instructions="You are the audit acceptance agent.",
            model_id=model.id,
        )
        session.add(agent)
        await session.flush()
        session.add(
            AgentAccessGrant(
                user_id=account.user_id, agent_id=agent.id, granted_by=account.user_id
            )
        )
        await session.commit()
        return _Identity(
            account_id=account.account_id,
            account_username=account.account_username,
            user_id=account.user_id,
            model_id=model.id,
            agent_id=agent.id,
        )


async def _create_run(
    factory: SessionFactory, identity: _Identity
) -> tuple[uuid.UUID, uuid.UUID]:
    """会话 + 已完成 run：TOOL/EGRESS/MODEL 三表经 run 归属 trace。"""
    from muad_agent_runtime.infrastructure.models.runtime import Conversation, RunRecord
    from muad_contracts import RunStatus

    now = datetime.now(UTC)
    async with factory() as session:
        conversation = Conversation(
            tenant_id=TENANT,
            user_id=identity.user_id,
            agent_id=identity.agent_id,
            title="Audit Acceptance",
        )
        session.add(conversation)
        await session.flush()
        run = RunRecord(
            tenant_id=TENANT,
            conversation_id=conversation.id,
            user_id=identity.user_id,
            agent_id=identity.agent_id,
            status=str(RunStatus.COMPLETED),
            input_text="audit acceptance seed",
            trace_id=TRACE_ID,
            start_time=now,
            end_time=now,
        )
        session.add(run)
        await session.flush()
        await session.commit()
        return conversation.id, run.id


async def _write_runtime_audits(factory: SessionFactory, context: AuditSeedContext) -> None:
    """TOOL / EGRESS / MODEL 三类（真实 `RuntimeAuditWriter`，各一独立短事务）。"""
    from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter

    now = datetime.now(UTC)
    writer = RuntimeAuditWriter(
        tenant_id=context.tenant_id,
        run_id=context.run_id,
        task_id=None,
        conversation_id=context.conversation_id,
        user_id=context.user_id,
        session_factory=lambda: factory,
    )
    await writer.record_tool_call(
        tool_call_id=f"call-{uuid.uuid4().hex[:12]}",
        tool_name=TOOL_NAME,
        tool_kind=TOOL_KIND,
        prepared_args_hash="sha256:" + "a" * 64,
        args_preview_json={"query": "ping"},
        status=TOOL_STATUS,
        start_time=now,
        end_time=now,
        latency_ms=12,
    )
    await writer.record_egress(
        target_type=EGRESS_TARGET_TYPE,
        target=EGRESS_TARGET,
        operation=EGRESS_OPERATION,
        method="POST",
        policy_decision="ALLOW",
        status_code=200,
        result_status=EGRESS_RESULT_STATUS,
        latency_ms=34,
    )
    await writer.record_model_invocation(
        provider="openai",
        model=MODEL_NAME,
        attempt=1,
        retry_reason=None,
        input_tokens=8,
        output_tokens=3,
        latency_ms=56,
        status=MODEL_STATUS,
    )


async def _write_audits(factory: SessionFactory, context: AuditSeedContext) -> AuditRowIds:
    """四类审计各一条（真实写入 port），再按 tenant 回读 id。"""
    from muad_api.audit import write_config_audit
    from muad_api.context import set_trace_context

    # CONFIG 的 trace 与 run 的 trace 同源：审计行可按同一 trace 串联排障
    set_trace_context(trace_id=context.trace_id)
    await _write_runtime_audits(factory, context)
    async with factory() as session:
        await write_config_audit(
            session,
            actor_user_id=context.account_id,
            resource_type=CONFIG_RESOURCE_TYPE,
            resource_id=context.agent_id,
            action=CONFIG_ACTION,
            before=CONFIG_ACTION_BEFORE,
            after=CONFIG_ACTION_AFTER,
            tenant_id=context.tenant_id,
            source_ip="127.0.0.1",
        )
        await session.commit()
    return await _read_row_ids(factory, context)


async def _read_row_ids(factory: SessionFactory, context: AuditSeedContext) -> AuditRowIds:
    """按 tenant 回读刚写入的四行 id（本租户各表仅一行，取回即真实落库行）。"""
    from muad_agent_runtime.infrastructure.models.runtime import (
        EgressAudit,
        ModelInvocationAudit,
        ToolCallAudit,
    )
    from muad_console_platform.infrastructure.models.control import ConfigAuditLog
    from sqlalchemy import select

    async with factory() as session:
        config_id = await session.scalar(
            select(ConfigAuditLog.id).where(ConfigAuditLog.tenant_id == context.tenant_id)
        )
        tool_id = await session.scalar(
            select(ToolCallAudit.id).where(ToolCallAudit.tenant_id == context.tenant_id)
        )
        egress_id = await session.scalar(
            select(EgressAudit.id).where(EgressAudit.tenant_id == context.tenant_id)
        )
        model_id = await session.scalar(
            select(ModelInvocationAudit.id).where(
                ModelInvocationAudit.tenant_id == context.tenant_id
            )
        )
    if config_id is None or tool_id is None or egress_id is None or model_id is None:
        raise RuntimeError("审计种子写入后未回读齐四类审计行（CONFIG/TOOL/EGRESS/MODEL）")
    return AuditRowIds(config=config_id, tool=tool_id, egress=egress_id, model=model_id)


async def _write_export(
    factory: SessionFactory, context: AuditSeedContext, artifact_root: Path
) -> ExportSeed:
    """按同一 trace 建导出任务并推进到 SUCCEEDED（产物落环境自有 artifact 根）。"""
    from muad_console_platform.application.audit_export_service import (
        AuditExportService,
        export_storage_key,
        query_filters_from_canonical,
        serialize_export_rows,
    )
    from muad_console_platform.application.dto import AuditExportCreateRequest
    from muad_console_platform.infrastructure.repositories.audit_export_repository import (
        AuditExportRepository,
    )
    from muad_console_platform.infrastructure.repositories.audit_query_repository import (
        AuditQueryRepository,
    )
    from muad_console_platform.infrastructure.skill_artifact_store import write_artifact

    async with factory() as session:
        service = AuditExportService(session, frozenset())
        created = await service.create_export(
            context.tenant_id,
            context.account_id,
            AuditExportCreateRequest(export_format=EXPORT_FORMAT, trace_id=context.trace_id),
            EXPORT_IDEMPOTENCY_KEY,
        )
        repository = AuditExportRepository(session)
        job = await repository.find_job(context.tenant_id, uuid.UUID(str(created["export_id"])))
        if job is None:
            raise RuntimeError("导出任务创建后未回读到任务行")
        filters = query_filters_from_canonical(job.filters_json)
        rows = await AuditQueryRepository(session).all_rows(context.tenant_id, filters)
        storage_key = export_storage_key(context.tenant_id, job.id, job.export_format)
        # 产物内容与生产执行器同源（同一投影 + 同一序列化），只把 artifact 根钉在验收环境
        write_artifact(
            storage_key, serialize_export_rows(rows, job.export_format), root=artifact_root
        )
        await repository.mark_running(job)
        await repository.mark_succeeded(job, row_count=len(rows), artifact_ref=storage_key)
        await session.commit()
        return ExportSeed(
            export_id=job.id,
            artifact_ref=storage_key,
            row_count=len(rows),
            idempotency_key=EXPORT_IDEMPOTENCY_KEY,
        )


async def seed_all(artifact_root: Path, *, model_base_url: str) -> AuditSeed:
    """在独占租户下写入一份完整审计种子；需真实 PostgreSQL 与可写 artifact 根。"""
    from muad_common import SharedSettings
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(SharedSettings().require_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        identity = await _create_agent(
            factory, await _create_account(factory), model_base_url
        )
        conversation_id, run_id = await _create_run(factory, identity)
        context = AuditSeedContext(
            tenant_id=TENANT,
            trace_id=TRACE_ID,
            account_id=identity.account_id,
            account_username=identity.account_username,
            user_id=identity.user_id,
            agent_id=identity.agent_id,
            model_id=identity.model_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )
        rows = await _write_audits(factory, context)
        export = await _write_export(factory, context, artifact_root)
    finally:
        await engine.dispose()
    return AuditSeed(
        context=context, rows=rows, export=export, account_password=ACCOUNT_PASSWORD
    )


__all__ = [
    "ACCOUNT_PASSWORD",
    "ACCOUNT_USERNAME",
    "AGENT_KEY",
    "AUDIT_ROW_COUNT",
    "AuditRowIds",
    "AuditSeed",
    "AuditSeedContext",
    "EGRESS_TARGET",
    "EXPORT_IDEMPOTENCY_KEY",
    "ExportSeed",
    "TENANT",
    "TOOL_NAME",
    "TRACE_ID",
    "seed_all",
]
