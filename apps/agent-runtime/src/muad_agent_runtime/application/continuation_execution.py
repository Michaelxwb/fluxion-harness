"""Rebuild from a frozen snapshot and canonical facts; only credentials are refreshed."""

from collections.abc import Awaitable, Callable
from functools import partial
from uuid import UUID

from muad_contracts import ResolvedAgent, ResolvedMcpServer, ResolvedModel, ResolvedSkill
from sqlalchemy import func, select

from ..infrastructure.db import get_session_factory
from ..infrastructure.models.runtime import CanonicalEvent, RunRecord, RunSubmission
from .async_tools.supervisor import ExecutionSupervisor
from .attachments.inbound import PersistedAttachment, attachments_from_payload
from .context_settings import compaction_settings_of
from .executor import ExecutorFactory
from .ports import CredentialsClient, PlatformSettingsClient, ResolveClient
from .run_service import RunService, execution_defaults_of, summary_model_of


def continuation_executor(
    resolve: ResolveClient,
    credentials: CredentialsClient,
    settings: PlatformSettingsClient,
    supervisor: ExecutionSupervisor,
    factory: ExecutorFactory,
    *,
    instance_id: str,
) -> Callable[[RunRecord], Awaitable[None]]:
    return partial(_execute, resolve, credentials, settings, supervisor, factory, instance_id)


async def _execute(
    resolve: ResolveClient,
    credentials: CredentialsClient,
    settings: PlatformSettingsClient,
    supervisor: ExecutionSupervisor,
    executor: ExecutorFactory,
    instance: str,
    run: RunRecord,
) -> None:
    from muad_api.context import TRACE_CORRELATION_FIELDS, context_scope

    fields = {name: "" for name in TRACE_CORRELATION_FIELDS}
    fields.update(
        trace_id=run.trace_id,
        run_id=str(run.id),
        conversation_id=str(run.conversation_id),
        platform_user_id=str(run.user_id),
        agent_id=str(run.agent_id),
        snapshot_id=str(run.snapshot_id),
    )
    with context_scope(tenant_id=run.tenant_id, caller_service="agent-runtime", **fields):
        async with get_session_factory()() as session:
            service = RunService(
                session,
                resolve,
                instance,
                settings,
                executor_factory=executor,
                credentials_client=credentials,
                supervisor=supervisor,
            )
            submission = (
                await session.scalars(
                    select(RunSubmission)
                    .where(
                        RunSubmission.run_id == run.id,
                        RunSubmission.tenant_id == run.tenant_id,
                        RunSubmission.is_deleted.is_(False),
                    )
                    .order_by(RunSubmission.create_time.desc(), RunSubmission.id)
                    .limit(1)
                )
            ).one()
            try:
                await _restore_and_run(service, run, submission.id)
            except Exception as exc:
                await service._finalize_failed(run, submission.id, exc, agent_key="continuation")


async def _restore_and_run(service: RunService, run: RunRecord, submission_id: UUID) -> None:
    snapshot = await service._load_snapshot(run)
    agent = ResolvedAgent.model_validate(snapshot.agent_json)
    model = ResolvedModel.model_validate(snapshot.model_json)
    skills = tuple(ResolvedSkill.model_validate(item) for item in snapshot.skill_catalog_json)
    servers = tuple(ResolvedMcpServer.model_validate(item) for item in snapshot.mcp_catalog_json)
    model, summary, secrets = await service._runtime_credentials(
        run.id,
        run.tenant_id,
        run.user_id,
        model,
        servers,
        summary_model=summary_model_of(snapshot.policy_json),
    )
    text, attachments = await _current_input(service, run, submission_id)
    stream = service._stream_run(
        run=run,
        agent=agent,
        model=model,
        skills=skills,
        mcp_servers=servers,
        mcp_secrets=secrets,
        history=(),
        submission_id=submission_id,
        resumed=run.execution_epoch > 1,
        compaction=compaction_settings_of(snapshot.policy_json),
        execution=execution_defaults_of(snapshot.policy_json),
        summary_model=summary,
        current_text=text,
        current_attachments=attachments,
    )
    async for _ in stream:
        pass


async def _current_input(
    service: RunService, run: RunRecord, submission_id: UUID
) -> tuple[str, tuple[PersistedAttachment, ...]]:
    async with get_session_factory()() as session:
        user = await session.scalar(select(CanonicalEvent).where(
            CanonicalEvent.tenant_id == run.tenant_id, CanonicalEvent.run_id == run.id,
            CanonicalEvent.submission_id == submission_id, CanonicalEvent.event_type == "USER_MESSAGE",
            CanonicalEvent.is_deleted.is_(False)).order_by(CanonicalEvent.seq.desc()).limit(1))
        waiting = await session.scalar(select(func.max(CanonicalEvent.seq)).where(
            CanonicalEvent.tenant_id == run.tenant_id, CanonicalEvent.run_id == run.id,
            CanonicalEvent.event_type == "RUN_WAITING_TOOL", CanonicalEvent.is_deleted.is_(False)))
    if user is None or user.seq <= (waiting or 0):
        return "", ()
    ids = {item.artifact_id for item in attachments_from_payload(user.payload_json.get("attachments"))}
    attachments = await service._load_inbound_attachments(run) if ids else ()
    selected = tuple(item for item in attachments if item.artifact_id in ids)
    return str(user.payload_json.get("text", "")), selected
