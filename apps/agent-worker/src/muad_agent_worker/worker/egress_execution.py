"""Worker-owned frozen snapshot/source binding to the embedded shared SDK."""

from __future__ import annotations

from dataclasses import replace

from muad_agent_core.sandbox.spec import TERMINATE_GRACE_SEC
from muad_agent_core.skill import SkillExecutionRequest, SkillExecutionResult, SkillExecutor
from muad_common import SharedSettings
from muad_contracts.egress_budget import EffectiveEgressBudget
from muad_contracts.egress_identity import egress_operation_key
from muad_platform_sdk.configured_egress import configured_host_egress
from muad_platform_sdk.egress_identity_registry import IdentityUnavailable
from muad_platform_sdk.egress_source_authority import SourceClaim
from muad_platform_sdk.host_egress_execution import HostEgressExecution
from muad_platform_sdk.shared_redis import MeteringUnavailable
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..infrastructure.models.task import TaskExecution
from .egress_source_lease import read_task_source_lease


def task_frozen_egress_budget(task: TaskExecution) -> EffectiveEgressBudget:
    snapshot = task.execution_snapshot_json
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("budget", {}), dict):
        raise MeteringUnavailable("frozen Task budget invalid")
    try:
        return EffectiveEgressBudget.model_validate(snapshot.get("budget", {}).get("egress_budget", {}))
    except ValidationError:
        raise MeteringUnavailable("frozen Task egress policy invalid") from None


def task_source_claim(task: TaskExecution) -> SourceClaim:
    if not isinstance(task.lease_owner, str) or not task.lease_owner:
        raise IdentityUnavailable("Task execution owner missing")
    return SourceClaim(task.tenant_id, "TASK", task.id, task.lease_owner, task.lease_epoch)


async def _bound_request(
    host: HostEgressExecution, task: TaskExecution, skill_key: str, request: SkillExecutionRequest
) -> SkillExecutionRequest:
    invocation = await host.host_invocation(
        operation_key=egress_operation_key("TASK_SKILL", str(task.id)), skill_key=skill_key
    )
    broker = host.broker(invocation, deadline_seconds=request.timeout_sec)
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


async def execute_task_skill_with_egress(
    executor: SkillExecutor,
    request: SkillExecutionRequest,
    *,
    task: TaskExecution,
    skill_key: str | None,
    settings: SharedSettings,
    sessions: async_sessionmaker[AsyncSession] | None,
) -> SkillExecutionResult:
    policy = task_frozen_egress_budget(task)
    if not policy.is_unlimited and skill_key is None:
        raise MeteringUnavailable("controlled Task requires a frozen Skill key")
    provider = None
    if sessions is not None:
        bound_sessions = sessions
        provider = lambda: bound_sessions  # noqa: E731 - lazy storage capture for disabled paths
    async with configured_host_egress(
        settings=settings,
        policy=policy,
        skill_keys=() if skill_key is None else (skill_key,),
        session_factory=provider,
        claim=lambda: task_source_claim(task),
        reader=read_task_source_lease,
        minimum_stop_margin_seconds=4 * TERMINATE_GRACE_SEC,
        tenant_id=task.tenant_id if task.execution_snapshot_json.get("egress_enabled") is True else None,
    ) as host:
        if host is None:
            return await executor.execute(request)
        if skill_key is None:
            raise MeteringUnavailable("controlled Task requires a frozen Skill key")

        async def controlled() -> SkillExecutionResult:
            return await executor.execute(await _bound_request(host, task, skill_key, request))

        return await host.run(controlled)
