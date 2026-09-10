from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class WorkspaceOwnerType(StrEnum):
    CONVERSATION = "conversation"
    EXECUTION = "execution"


class WorkspaceStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    RELEASED = "released"


class Workspace(BaseModel):
    id: UUID
    tenant_id: str
    owner_type: WorkspaceOwnerType
    owner_id: UUID
    storage_backend: str
    root_ref: str
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE
    created_at: datetime
    expires_at: datetime | None = None
