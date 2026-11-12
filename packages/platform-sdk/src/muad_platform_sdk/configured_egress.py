"""Lazy process-embedded assembly from host settings and a frozen snapshot."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace

from muad_common import SharedSettings
from muad_common.redis_client import create_redis_client
from muad_contracts.egress_budget import EffectiveEgressBudget
from muad_contracts.egress_identity import freeze_egress_identity_budgets
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .console_egress import load_console_egress_policy, resolve_policy_target, tenant_domain
from .egress_domain import provision_tenant_domain
from .egress_ipc import AttemptMetadata, EgressTarget
from .egress_runtime_config import _authority_matches, load_egress_runtime_config
from .egress_source_authority import SourceClaim, SourceReader
from .host_egress_execution import HostEgressExecution
from .shared_redis import MeteringUnavailable, SharedRedisMeter

logger = logging.getLogger(__name__)


async def _close_host(root: HostEgressExecution | None, margin: float, *, already_failed: bool) -> None:
    if root is None:
        return
    try:
        async with asyncio.timeout(margin):
            await root.aclose()
    except Exception:
        logger.error("egress_host_cleanup_unconfirmed")
        if not already_failed:
            raise MeteringUnavailable("host termination not confirmed") from None


@asynccontextmanager
async def configured_host_egress(
    *,
    settings: SharedSettings,
    policy: EffectiveEgressBudget,
    skill_keys: Sequence[str],
    session_factory: Callable[[], async_sessionmaker[AsyncSession]] | None,
    claim: Callable[[], SourceClaim],
    reader: SourceReader,
    minimum_stop_margin_seconds: float,
    tenant_id: str | None = None,
) -> AsyncIterator[HostEgressExecution | None]:
    try:
        budget = freeze_egress_identity_budgets(policy, skill_keys=skill_keys)
    except ValueError:
        raise MeteringUnavailable("frozen egress budget configuration unsupported") from None
    public_policy = await load_console_egress_policy(settings, tenant_id) if tenant_id else None
    if public_policy is not None and not public_policy.enabled and not budget.enabled:
        yield None
        return
    if settings.egress_control_file is None:
        if budget.enabled or (public_policy is not None and public_policy.enabled):
            raise MeteringUnavailable("shared metering configuration required")
        yield None
        return
    config = await load_egress_runtime_config(settings.egress_control_file)
    public_enabled = public_policy.enabled if public_policy is not None else config.public_rules_enabled
    if not public_enabled and not budget.enabled:
        yield None
        return
    domain = tenant_domain(config.domain, tenant_id) if tenant_id else config.domain
    if (
        session_factory is None
        or config.stop_margin_seconds < minimum_stop_margin_seconds + config.cleanup_seconds
    ):
        raise MeteringUnavailable("host storage and sufficient shutdown margin required")
    source = claim()  # Capture trusted ownership only for an enabled execution.
    seeds = [s.strip() for s in settings.redis_cluster_seeds.split(",") if s.strip()]
    redis = create_redis_client(
        settings.redis_url,
        mode=settings.redis_mode,
        seeds=seeds,
        max_connections=settings.redis_max_connections,
    )
    root = None
    try:
        meter = SharedRedisMeter(redis, domain=domain, epoch=config.epoch)
        if public_policy is not None and tenant_id is not None:
            await provision_tenant_domain(meter, session_factory(), tenant_id)
            try:
                await meter.publish(public_policy)
            except MeteringUnavailable:
                # A concurrent newer publication is safe; missing/corrupt state is not.
                current = await meter.current_policy()
                if current.revision <= public_policy.revision:
                    raise

        async def resolve(request: AttemptMetadata) -> EgressTarget | None:
            if public_policy is None:
                return config.resolve(request)
            if not _authority_matches(request):
                return None
            current = await meter.current_policy()
            selected = resolve_policy_target(current, request)
            if selected is not None:
                return selected
            # No matching interface limit: execution totals still count real sends.
            return EgressTarget("__unmatched__", "__unmatched__", current)

        root = await HostEgressExecution.create(
            settings=replace(config.host_settings(), domain=domain, public_rules_enabled=public_enabled),
            budget=budget,
            sessions=session_factory(),
            redis=redis,
            claim=source,
            reader=reader,
            resolve=resolve,
        )
        yield root
    finally:
        try:
            await _close_host(root, config.stop_margin_seconds, already_failed=sys.exception() is not None)
        finally:
            await redis.aclose()
