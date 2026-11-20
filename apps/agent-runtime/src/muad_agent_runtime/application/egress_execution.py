"""Run-owned source identity and lazy shared SDK assembly."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from functools import partial
from typing import TYPE_CHECKING

from muad_agent_core.sandbox.spec import TERMINATE_GRACE_SEC
from muad_common import process_settings
from muad_contracts.egress_budget import EffectiveEgressBudget
from muad_platform_sdk.configured_egress import configured_host_egress
from muad_platform_sdk.console_egress import load_platform_trace_base_urls
from muad_platform_sdk.egress_attempt_audit import AttemptObserver, EgressAttempt
from muad_platform_sdk.egress_audit_store import DatabaseAttemptObserver, EgressAuditIdentity
from muad_platform_sdk.egress_identity_registry import IdentityUnavailable
from muad_platform_sdk.egress_ipc import PlatformBaseURLLoader
from muad_platform_sdk.egress_source_authority import SourceClaim
from muad_platform_sdk.host_egress_execution import HostEgressExecution

from ..infrastructure.db import get_session_factory
from .egress_source_lease import read_run_source_lease

if TYPE_CHECKING:
    from .executor import ExecutorRequest


def run_source_claim(request: ExecutorRequest) -> SourceClaim:
    context, identity = request.run_context, request.execution_identity
    if (
        context is None
        or identity is None
        or context.tenant_id != identity.tenant_id
        or context.run_id != identity.run_id
    ):
        raise IdentityUnavailable("trusted Run execution ownership required")
    return SourceClaim(context.tenant_id, "RUN", context.run_id, identity.owner, identity.epoch)


def run_trace_observer(request: ExecutorRequest) -> AttemptObserver | None:
    context = request.run_context
    if context is None:
        return None
    identity = EgressAuditIdentity(context.tenant_id, context.user_id, context.run_id)

    async def observe(attempt: EgressAttempt) -> None:
        await DatabaseAttemptObserver(
            get_session_factory(), identity, timeout_seconds=process_settings().egress_cleanup_sec,
        )(attempt)

    return observe


def run_platform_trace_loader(request: ExecutorRequest) -> PlatformBaseURLLoader | None:
    if request.run_context is None:
        return None
    return partial(load_platform_trace_base_urls, process_settings(), request.run_context.tenant_id)


def run_egress(request: ExecutorRequest) -> AbstractAsyncContextManager[HostEgressExecution | None]:
    policy = request.execution.egress_budget if request.execution is not None else EffectiveEgressBudget()
    return configured_host_egress(
        settings=process_settings(),
        policy=policy,
        skill_keys=tuple(skill.key for skill in request.skills),
        session_factory=get_session_factory,
        claim=lambda: run_source_claim(request),
        reader=read_run_source_lease,
        audit_identity=EgressAuditIdentity(
            request.run_context.tenant_id, request.run_context.user_id, request.run_context.run_id
        ) if request.run_context is not None else None,
        minimum_stop_margin_seconds=4 * TERMINATE_GRACE_SEC,
        tenant_id=(
            request.run_context.tenant_id
            if request.run_context is not None
            and request.execution is not None
            and request.execution.egress_enabled
            else None
        ),
    )
