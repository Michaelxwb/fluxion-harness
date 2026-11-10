"""Skill 草稿内部 API（API-03 / API-12）：Runtime 工具与 Console 内部调用。"""

from __future__ import annotations

import base64
import binascii
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from muad_api import ApiResponse, AppError, InternalServiceDep, ok
from muad_api.error_codes import ErrorCode
from muad_contracts import ChannelName
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.skill_draft_service import DraftChange, SkillDraftService
from ..infrastructure.db import get_session

Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/internal/skills/drafts", tags=["internal-skill-drafts"])


class DraftCreateRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    owner_user_id: uuid.UUID
    agent_id: uuid.UUID
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


class DraftChangePayload(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    content: str | None = None
    content_base64: str | None = None


class DraftApplyRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    owner_user_id: uuid.UUID
    expected_revision: int = Field(ge=1)
    changes: list[DraftChangePayload] = Field(default_factory=list, max_length=10000)
    deletions: list[str] = Field(default_factory=list, max_length=10000)


@router.post("")
async def create_draft(
    payload: DraftCreateRequest,
    request: Request,
    session: Session,
    _internal: InternalServiceDep,
) -> ApiResponse[Any]:
    data = await SkillDraftService(session).create(
        payload.tenant_id, payload.owner_user_id, payload.agent_id, payload.key
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


@router.get("/{draft_id}/archive")
async def archive_draft(
    draft_id: uuid.UUID,
    session: Session,
    _internal: InternalServiceDep,
    tenant_id: Annotated[str, Query(min_length=1, max_length=64)],
    owner_user_id: Annotated[uuid.UUID, Query()],
) -> Response:
    """草稿 Shell 取件：zip 与 revision 同一响应，revision 走响应头。"""
    view, data = await SkillDraftService(session).archive(tenant_id, owner_user_id, draft_id)
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "X-Draft-Revision": str(view["revision"]),
            "X-Draft-Agent-Id": str(view["agent_id"]),
            "X-Draft-File-Count": str(view["file_count"]),
            "X-Draft-Total-Size": str(view["total_size"]),
            "Content-Disposition": f'attachment; filename="draft-{draft_id}-r{view["revision"]}.zip"',
        },
    )


@router.post("/{draft_id}/apply")
async def apply_draft_changes(
    draft_id: uuid.UUID,
    payload: DraftApplyRequest,
    request: Request,
    session: Session,
    _internal: InternalServiceDep,
) -> ApiResponse[Any]:
    """草稿 Shell 回写：一次批量变更 + expected_revision CAS。"""
    data = await SkillDraftService(session).apply_changes(
        payload.tenant_id,
        payload.owner_user_id,
        draft_id,
        expected_revision=payload.expected_revision,
        changes=[_decode_change(change) for change in payload.changes],
        deletions=payload.deletions,
    )
    return ok(request.app.state.message_catalog, data)


def _decode_change(change: DraftChangePayload) -> DraftChange:
    if (change.content is None) == (change.content_base64 is None):
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    if change.content is not None:
        return DraftChange(path=change.path, content=change.content.encode("utf-8"))
    assert change.content_base64 is not None
    try:
        content = base64.b64decode(change.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from exc
    return DraftChange(path=change.path, content=content)


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
