"""Worker-owned frozen snapshot/source binding to the embedded shared SDK."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from functools import partial

from muad_agent_core.sandbox.spec import TERMINATE_GRACE_SEC
from muad_agent_core.skill import SkillExecutionRequest, SkillExecutionResult, SkillExecutor
from muad_common import SharedSettings
from muad_platform_sdk.configured_egress import configured_host_egress
from muad_platform_sdk.console_egress import load_platform_trace_base_urls
from muad_platform_sdk.egress_audit_store import DatabaseAttemptObserver, EgressAuditIdentity
from muad_platform_sdk.egress_identity_registry import IdentityUnavailable
from muad_platform_sdk.egress_ipc import InvocationEgressBroker, PlatformBaseURLLoader
from muad_platform_sdk.egress_source_authority import SourceClaim
from muad_platform_sdk.host_egress_execution import HostEgressExecution
from muad_platform_sdk.shared_redis import MeteringUnavailable
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..application.agentic.egress_budget import task_frozen_egress_budget
from ..infrastructure.models.task import TaskExecution
from .egress_source_lease import read_task_source_lease


def task_source_claim(task: TaskExecution) -> SourceClaim:
    if not isinstance(task.lease_owner, str) or not task.lease_owner:
        raise IdentityUnavailable("Task execution owner missing")
    return SourceClaim(task.tenant_id, "TASK", task.id, task.lease_owner, task.lease_epoch)


async def _bound_request(
    host: HostEgressExecution, skill_key: str, request: SkillExecutionRequest,
    load_platform_base_urls: PlatformBaseURLLoader,
    operation_key: str,
) -> SkillExecutionRequest:
    invocation = await host.host_invocation(
        operation_key=operation_key, skill_key=skill_key
    )
    broker = host.broker(
        invocation, deadline_seconds=request.timeout_sec, load_platform_base_urls=load_platform_base_urls,
    )
    context = request.platform_context
    if context is not None:
        context = replace(
            context,
            transport_factory=lambda verify: host.transport(
                invocation,
                verify=verify,
                on_limit=broker.note_limit,
            ),
        )
    return replace(request, egress_broker=broker, platform_context=context)


def task_egress(
    task: TaskExecution, settings: SharedSettings,
    sessions: async_sessionmaker[AsyncSession] | None, skill_keys: Sequence[str],
) -> AbstractAsyncContextManager[HostEgressExecution | None]:
    """One owned source segment for all outbound work of this claimed Execution."""
    provider = None
    if sessions is not None:
        bound_sessions = sessions
        provider = lambda: bound_sessions  # noqa: E731 - lazy storage capture for disabled paths
    identity = EgressAuditIdentity(task.tenant_id, task.actor_user_id, task.source_run_id, task.id)
    return configured_host_egress(
        settings=settings,
        policy=task_frozen_egress_budget(task),
        skill_keys=skill_keys,
        session_factory=provider,
        claim=lambda: task_source_claim(task),
        reader=read_task_source_lease,
        audit_identity=identity,
        minimum_stop_margin_seconds=4 * TERMINATE_GRACE_SEC,
        tenant_id=task.tenant_id if task.execution_snapshot_json.get("egress_enabled") is True else None,
    )


async def execute_task_skill_with_egress(
    executor: SkillExecutor, request: SkillExecutionRequest, *, task: TaskExecution,
    skill_key: str | None, settings: SharedSettings,
    sessions: async_sessionmaker[AsyncSession] | None,
    operation_key: str, egress: HostEgressExecution | None = None,
) -> SkillExecutionResult:
    if skill_key is None and (egress is not None or not task_frozen_egress_budget(task).is_unlimited):
        raise MeteringUnavailable("controlled Task requires a frozen Skill key")
    loader = partial(load_platform_trace_base_urls, settings, task.tenant_id)
    if egress is not None:
        assert skill_key is not None
        return await executor.execute(await _bound_request(
            egress, skill_key, request, loader, operation_key,
        ))
    async with task_egress(task, settings, sessions, () if skill_key is None else (skill_key,)) as host:
        if host is None:
            if sessions is not None:
                cleanup = settings.egress_cleanup_sec
                identity = EgressAuditIdentity(
                    task.tenant_id, task.actor_user_id, task.source_run_id, task.id,
                )
                observer = DatabaseAttemptObserver(sessions, identity, timeout_seconds=cleanup)
                broker = InvocationEgressBroker.for_tracing(
                    observe=observer, deadline_seconds=request.timeout_sec, cleanup_seconds=cleanup,
                    load_platform_base_urls=loader,
                )
                request = replace(request, egress_broker=broker)
            return await executor.execute(request)
        if skill_key is None:
            raise MeteringUnavailable("controlled Task requires a frozen Skill key")

        async def controlled() -> SkillExecutionResult:
            return await executor.execute(await _bound_request(
                host, skill_key, request, loader, operation_key,
            ))

        return await host.run(controlled)
