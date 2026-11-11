"""Run-owned PG reader for SDK authority; leases use Run execution_epoch."""
import sqlalchemy as sa
from muad_platform_sdk.egress_identity_registry import IdentityUnavailable
from muad_platform_sdk.egress_source_authority import SourceClaim, SourceLease
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from ..infrastructure.models.runtime import RunRecord


async def read_run_source_lease(session: AsyncSession, claim: SourceClaim) -> SourceLease:
    if claim.source_kind != "RUN":
        raise IdentityUnavailable("invalid Run source")
    run = await session.scalar(sa.select(RunRecord).options(load_only(
        RunRecord.tenant_id, RunRecord.id, RunRecord.lease_owner, RunRecord.execution_epoch,
        RunRecord.status, RunRecord.lease_until, RunRecord.deadline_at,
        RunRecord.cancel_requested, RunRecord.is_deleted,
    )).where(RunRecord.tenant_id == claim.tenant_id,
             RunRecord.id == claim.source_id).with_for_update().execution_options(populate_existing=True))
    if run is None:
        raise IdentityUnavailable("Run source unavailable")
    return SourceLease(
        tenant_id=run.tenant_id, source_kind="RUN", source_id=run.id, owner=run.lease_owner,
        epoch=run.execution_epoch, status=str(run.status), expires_at=run.lease_until,
        deadline_at=run.deadline_at, cancel_requested=run.cancel_requested, is_deleted=run.is_deleted,
    )
