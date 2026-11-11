"""Run-owned source identity and lazy shared SDK assembly."""

from __future__ import annotations

import json
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING

from muad_agent_core.sandbox.spec import TERMINATE_GRACE_SEC
from muad_common import process_settings
from muad_contracts.egress_budget import EffectiveEgressBudget
from muad_platform_sdk.configured_egress import configured_host_egress
from muad_platform_sdk.egress_identity_registry import IdentityUnavailable
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


def run_egress(request: ExecutorRequest) -> AbstractAsyncContextManager[HostEgressExecution | None]:
    policy = request.execution.egress_budget if request.execution is not None else EffectiveEgressBudget()
    return configured_host_egress(
        settings=process_settings(),
        policy=policy,
        skill_keys=tuple(skill.key for skill in request.skills),
        session_factory=get_session_factory,
        claim=lambda: run_source_claim(request),
        reader=read_run_source_lease,
        minimum_stop_margin_seconds=4 * TERMINATE_GRACE_SEC,
        route_overrides=(
            json.loads(request.execution.egress_rules_json)
            if request.execution is not None and request.execution.egress_enabled
            else ()
        ),
    )
