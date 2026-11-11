"""Task-owned PG reader for SDK authority; no script-supplied execution identity."""
import sqlalchemy as sa
from muad_platform_sdk.egress_identity_registry import IdentityUnavailable
from muad_platform_sdk.egress_source_authority import SourceClaim, SourceLease
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from ..infrastructure.models.task import TaskExecution


async def read_task_source_lease(session: AsyncSession, claim: SourceClaim) -> SourceLease:
    if claim.source_kind != "TASK":
        raise IdentityUnavailable("invalid Task source")
    task = await session.scalar(sa.select(TaskExecution).options(load_only(
        TaskExecution.tenant_id, TaskExecution.id, TaskExecution.lease_owner, TaskExecution.lease_epoch,
        TaskExecution.status, TaskExecution.lease_until, TaskExecution.deadline_at,
        TaskExecution.cancel_requested, TaskExecution.is_deleted,
    )).where(TaskExecution.tenant_id == claim.tenant_id,
             TaskExecution.id == claim.source_id).with_for_update().execution_options(populate_existing=True))
    if task is None:
        raise IdentityUnavailable("Task source unavailable")
    return SourceLease(
        tenant_id=task.tenant_id, source_kind="TASK", source_id=task.id, owner=task.lease_owner,
        epoch=task.lease_epoch, status=task.status, expires_at=task.lease_until,
        deadline_at=task.deadline_at, cancel_requested=task.cancel_requested, is_deleted=task.is_deleted,
    )
