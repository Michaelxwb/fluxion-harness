"""Lazy process-embedded assembly from host settings and a frozen snapshot."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager

from muad_common import SharedSettings
from muad_common.redis_client import create_redis_client
from muad_contracts.egress_budget import EffectiveEgressBudget
from muad_contracts.egress_identity import freeze_egress_identity_budgets
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .egress_runtime_config import load_egress_runtime_config
from .egress_source_authority import SourceClaim, SourceReader
from .host_egress_execution import HostEgressExecution
from .shared_redis import MeteringUnavailable

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
) -> AsyncIterator[HostEgressExecution | None]:
    try:
        budget = freeze_egress_identity_budgets(policy, skill_keys=skill_keys)
    except ValueError:
        raise MeteringUnavailable("frozen egress budget configuration unsupported") from None
    if settings.egress_control_file is None:
        if budget.enabled:
            raise MeteringUnavailable("shared metering configuration required")
        yield None
        return
    config = await load_egress_runtime_config(settings.egress_control_file)
    if not config.public_rules_enabled and not budget.enabled:
        yield None
        return
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
        root = await HostEgressExecution.create(
            settings=config.host_settings(),
            budget=budget,
            sessions=session_factory(),
            redis=redis,
            claim=source,
            reader=reader,
            resolve=config.resolve,
        )
        yield root
    finally:
        try:
            await _close_host(root, config.stop_margin_seconds, already_failed=sys.exception() is not None)
        finally:
            await redis.aclose()
