from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from framework.web.errors import AppError
from framework.workspace.models import Workspace, WorkspaceOwnerType, WorkspaceStatus


class LocalWorkspaceManager:
    """Development-only workspace manager; production must use durable metadata storage."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir.resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._items: dict[UUID, Workspace] = {}

    async def create(
        self,
        *,
        tenant_id: str,
        owner_type: WorkspaceOwnerType,
        owner_id: UUID,
        ttl_seconds: int | None = None,
    ) -> Workspace:
        workspace_id = uuid4()
        path = self.base_dir / str(workspace_id)
        path.mkdir(parents=True, exist_ok=False)
        now = datetime.now(UTC)
        workspace = Workspace(
            id=workspace_id,
            tenant_id=tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
            storage_backend="local-dev",
            root_ref=str(path),
            created_at=now,
            expires_at=(now + timedelta(seconds=ttl_seconds)) if ttl_seconds else None,
        )
        self._items[workspace_id] = workspace
        return workspace

    async def get(self, workspace_id: UUID) -> Workspace:
        workspace = self._items.get(workspace_id)
        if workspace is None:
            raise AppError(code="WORKSPACE_NOT_FOUND", message="workspace does not exist", status_code=404)
        if workspace.status is not WorkspaceStatus.ACTIVE:
            raise AppError(code="WORKSPACE_INACTIVE", message="workspace is not active", status_code=409)
        if workspace.expires_at and workspace.expires_at <= datetime.now(UTC):
            workspace.status = WorkspaceStatus.EXPIRED
            raise AppError(code="WORKSPACE_EXPIRED", message="workspace has expired", status_code=410)
        return workspace

    async def release(self, workspace_id: UUID) -> None:
        workspace = await self.get(workspace_id)
        workspace.status = WorkspaceStatus.RELEASED
