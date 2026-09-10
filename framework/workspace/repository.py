from typing import Protocol
from uuid import UUID

from framework.workspace.models import Workspace


class WorkspaceRepository(Protocol):
    async def create(self, workspace: Workspace) -> None: ...
    async def get(self, workspace_id: UUID) -> Workspace | None: ...
    async def update(self, workspace: Workspace) -> None: ...
