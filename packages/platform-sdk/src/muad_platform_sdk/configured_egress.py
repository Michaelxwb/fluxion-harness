"""Embedded SDK assembly: Settings own lifecycle; Console owns HTTP rules."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager

from muad_common import SharedSettings
from muad_common.redis_client import create_redis_client
from muad_contracts.egress_budget import EffectiveEgressBudget
from muad_contracts.egress_identity import freeze_egress_identity_budgets
from muad_contracts.shared_egress import FrozenIdentityBudgets, SharedEgressPolicy
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .console_egress import load_console_egress_policy, resolve_policy_target, tenant_domain
from .egress_domain import provision_tenant_domain
from .egress_http_routes import _authority_matches
from .egress_ipc import AttemptMetadata, EgressTarget
from .egress_source_authority import SourceClaim, SourceReader
from .host_egress_execution import HostEgressExecution, HostEgressSettings
from .shared_http_transport import RequestLifetime
from .shared_redis import MeteringUnavailable, SharedRedisMeter

logger = logging.getLogger(__name__)


def host_settings(
    settings: SharedSettings, *, public_enabled: bool, tenant_id: str | None
) -> HostEgressSettings:
    return HostEgressSettings(
        domain=tenant_domain(settings.egress_meter_domain, tenant_id)
        if tenant_id
        else settings.egress_meter_domain,
        epoch=settings.egress_meter_epoch,
        public_rules_enabled=public_enabled,
        lifetime=RequestLifetime(
            settings.egress_http_read_timeout_sec,
            settings.egress_cleanup_sec,
            settings.egress_renewal_interval_sec,
        ),
        source_refresh_seconds=settings.egress_source_refresh_sec,
        stop_margin_seconds=settings.egress_stop_margin_sec,
    )


async def _publish_policy(
    meter: SharedRedisMeter,
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: str,
    policy: SharedEgressPolicy | None,
) -> None:
    created = await provision_tenant_domain(meter, sessions, tenant_id)
    if policy is None and not created:
        return
    requested = policy or SharedEgressPolicy()
    try:
        await meter.publish(requested)
    except MeteringUnavailable:
        current = await meter.current_policy()
        if current.revision <= requested.revision:
            raise


def _resolver(meter: SharedRedisMeter) -> Callable[[AttemptMetadata], Awaitable[EgressTarget | None]]:
    async def resolve(request: AttemptMetadata) -> EgressTarget | None:
        if not _authority_matches(request):
            return None
        current = await meter.current_policy()
        selected = resolve_policy_target(current, request)
        return selected or EgressTarget("__unmatched__", "__unmatched__", current)

    return resolve


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
async def _active_host(
    settings: SharedSettings,
    config: HostEgressSettings,
    budget: FrozenIdentityBudgets,
    sessions: async_sessionmaker[AsyncSession],
    source: SourceClaim,
    reader: SourceReader,
    public_policy: SharedEgressPolicy | None,
) -> AsyncIterator[HostEgressExecution | None]:
    redis = create_redis_client(
        settings.redis_url,
        mode=settings.redis_mode,
        seeds=[item.strip() for item in settings.redis_cluster_seeds.split(",") if item.strip()],
        max_connections=settings.redis_max_connections,
    )
    root = None
    try:
        meter = SharedRedisMeter(redis, domain=config.domain, epoch=config.epoch)
        await _publish_policy(meter, sessions, source.tenant_id, public_policy)
        root = await HostEgressExecution.create(
            settings=config,
            budget=budget,
            sessions=sessions,
            redis=redis,
            claim=source,
            reader=reader,
            resolve=_resolver(meter),
        )
        yield root
    finally:
        try:
            await _close_host(root, config.stop_margin_seconds, already_failed=sys.exception() is not None)
        finally:
            await redis.aclose()


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
    public_enabled = public_policy is not None and public_policy.enabled
    if not public_enabled and not budget.enabled:
        yield None
        return
    try:
        config = host_settings(settings, public_enabled=public_enabled, tenant_id=tenant_id)
    except ValueError:
        raise MeteringUnavailable("invalid shared metering lifecycle settings") from None
    if (
        session_factory is None
        or config.stop_margin_seconds < minimum_stop_margin_seconds + settings.egress_cleanup_sec
    ):
        raise MeteringUnavailable("host storage and sufficient shutdown margin required")
    async with _active_host(
        settings, config, budget, session_factory(), claim(), reader, public_policy
    ) as root:
        yield root
