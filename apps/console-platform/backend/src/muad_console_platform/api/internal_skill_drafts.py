"""Skill 草稿内部 API（API-03 / API-12）：Runtime 工具与 Console 内部调用。"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from muad_api import ApiResponse, InternalServiceDep, ok
from muad_contracts import ChannelName
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.skill_draft_service import SkillDraftService
from ..infrastructure.db import get_session

Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/internal/skills/drafts", tags=["internal-skill-drafts"])


class DraftCreateRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    owner_user_id: uuid.UUID
    key: str = Field(min_length=1, max_length=128)


class DraftFileWriteRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    owner_user_id: uuid.UUID
    path: str = Field(min_length=1, max_length=1024)
    content: str
    expected_revision: int = Field(ge=1)


class DraftReviewRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    owner_user_id: uuid.UUID
    default_script: str | None = Field(default=None, max_length=256)


class DraftSubmitRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    owner_user_id: uuid.UUID
    expected_revision: int = Field(ge=1)
    version: str | None = Field(default=None, min_length=1, max_length=64)
    default_script: str | None = Field(default=None, max_length=256)
    submitted_agent_id: uuid.UUID | None = None
    submitted_channel: ChannelName | None = Field(default=None, max_length=16)


@router.post("")
async def create_draft(
    payload: DraftCreateRequest,
    request: Request,
    session: Session,
    _internal: InternalServiceDep,
) -> ApiResponse[Any]:
    data = await SkillDraftService(session).create(
        payload.tenant_id, payload.owner_user_id, payload.key
    )
    return ok(request.app.state.message_catalog, data)


@router.get("/{draft_id}")
async def get_draft(
    draft_id: uuid.UUID,
    request: Request,
    session: Session,
    _internal: InternalServiceDep,
    tenant_id: Annotated[str, Query(min_length=1, max_length=64)],
    owner_user_id: Annotated[uuid.UUID, Query()],
) -> ApiResponse[Any]:
    data = await SkillDraftService(session).metadata(tenant_id, owner_user_id, draft_id)
    return ok(request.app.state.message_catalog, data)


@router.get("/{draft_id}/files")
async def read_draft_file(
    draft_id: uuid.UUID,
    request: Request,
    session: Session,
    _internal: InternalServiceDep,
    tenant_id: Annotated[str, Query(min_length=1, max_length=64)],
    owner_user_id: Annotated[uuid.UUID, Query()],
    path: Annotated[str, Query(max_length=1024)] = "",
) -> ApiResponse[Any]:
    data = await SkillDraftService(session).read(tenant_id, owner_user_id, draft_id, path)
    return ok(request.app.state.message_catalog, data)


@router.put("/{draft_id}/files")
async def write_draft_file(
    draft_id: uuid.UUID,
    payload: DraftFileWriteRequest,
    request: Request,
    session: Session,
    _internal: InternalServiceDep,
) -> ApiResponse[Any]:
    data = await SkillDraftService(session).write(
        payload.tenant_id,
        payload.owner_user_id,
        draft_id,
        path=payload.path,
        content=payload.content,
        expected_revision=payload.expected_revision,
    )
    return ok(request.app.state.message_catalog, data)


@router.delete("/{draft_id}/files")
async def delete_draft_file(
    draft_id: uuid.UUID,
    request: Request,
    session: Session,
    _internal: InternalServiceDep,
    tenant_id: Annotated[str, Query(min_length=1, max_length=64)],
    owner_user_id: Annotated[uuid.UUID, Query()],
    path: Annotated[str, Query(min_length=1, max_length=1024)],
    expected_revision: Annotated[int | None, Query(ge=1)] = None,
) -> ApiResponse[Any]:
    data = await SkillDraftService(session).delete_file(
        tenant_id,
        owner_user_id,
        draft_id,
        path=path,
        expected_revision=expected_revision,
    )
    return ok(request.app.state.message_catalog, data)


@router.delete("/{draft_id}")
async def delete_draft(
    draft_id: uuid.UUID,
    request: Request,
    session: Session,
    _internal: InternalServiceDep,
    tenant_id: Annotated[str, Query(min_length=1, max_length=64)],
    owner_user_id: Annotated[uuid.UUID, Query()],
) -> ApiResponse[Any]:
    data = await SkillDraftService(session).delete_draft(tenant_id, owner_user_id, draft_id)
    return ok(request.app.state.message_catalog, data)


@router.post("/{draft_id}/review")
async def review_draft(
    draft_id: uuid.UUID,
    payload: DraftReviewRequest,
    request: Request,
    session: Session,
    _internal: InternalServiceDep,
) -> ApiResponse[Any]:
    data = await SkillDraftService(session).review(
        payload.tenant_id,
        payload.owner_user_id,
        draft_id,
        default_script=payload.default_script,
    )
    return ok(request.app.state.message_catalog, data)


@router.post("/{draft_id}/submit")
async def submit_draft(
    draft_id: uuid.UUID,
    payload: DraftSubmitRequest,
    request: Request,
    session: Session,
    _internal: InternalServiceDep,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
) -> ApiResponse[Any]:
    data = await SkillDraftService(session).submit(
        payload.tenant_id,
        payload.owner_user_id,
        draft_id,
        expected_revision=payload.expected_revision,
        version=payload.version,
        default_script=payload.default_script,
        submitted_agent_id=payload.submitted_agent_id,
        submitted_channel=payload.submitted_channel,
        idempotency_key=idempotency_key,
    )
    return ok(request.app.state.message_catalog, data)


@router.get("/{draft_id}/status")
async def draft_status(
    draft_id: uuid.UUID,
    request: Request,
    session: Session,
    _internal: InternalServiceDep,
    tenant_id: Annotated[str, Query(min_length=1, max_length=64)],
    owner_user_id: Annotated[uuid.UUID, Query()],
) -> ApiResponse[Any]:
    data = await SkillDraftService(session).status(tenant_id, owner_user_id, draft_id)
    return ok(request.app.state.message_catalog, data)
