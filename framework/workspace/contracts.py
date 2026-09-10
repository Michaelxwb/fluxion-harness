from typing import Protocol
from uuid import UUID

from framework.workspace.models import Workspace, WorkspaceOwnerType


class WorkspaceManager(Protocol):
    async def create(
        self,
        *,
        tenant_id: str,
        owner_type: WorkspaceOwnerType,
        owner_id: UUID,
        ttl_seconds: int | None = None,
    ) -> Workspace: ...

    async def get(self, workspace_id: UUID) -> Workspace: ...

    async def release(self, workspace_id: UUID) -> None: ...
