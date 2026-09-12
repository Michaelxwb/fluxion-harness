from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from adapters.postgres.agent_repository import AgentRepository
from adapters.postgres.models import AgentDefinitionModel
from apps.platform_api.dependencies import get_session_factory, require_builder
from framework.observability.context import request_id_ctx
from framework.web.pagination import PageData
from framework.web.response import ApiResponse, ok

router = APIRouter(
    prefix="/agents",
    tags=["agents"],
    dependencies=[Depends(require_builder())],
)


class AgentUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=4000)
    instructions: str = Field(min_length=1)
    model_config_id: UUID | None = None
    memory_policy: dict[str, object] = Field(default_factory=dict)


class AgentSave(AgentUpsert):
    """Update body.

    ``revision`` is required: the caller must state the revision it read, so a
    concurrent edit conflicts (409) instead of silently clobbering — last write
    wins with no signal.
    """

    revision: int = Field(ge=1)


class AgentSummary(BaseModel):
    id: UUID
    name: str
    description: str
    revision: int
    enabled: bool
    create_time: str
    update_time: str


def _repo() -> AgentRepository:
    return AgentRepository(get_session_factory())


def _summary(row: AgentDefinitionModel) -> AgentSummary:
    return AgentSummary(
        id=row.id,
        name=row.name,
        description=row.description,
        revision=row.revision,
        enabled=row.enabled,
        create_time=row.create_time.isoformat(),
        update_time=row.update_time.isoformat(),
    )


@router.get("", response_model=ApiResponse[PageData[AgentSummary]])
async def list_agents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    keyword: str | None = Query(default=None, max_length=256),
    enabled: bool | None = Query(default=None),
) -> ApiResponse[PageData[AgentSummary]]:
    rows, total = await _repo().list(page=page, page_size=page_size, keyword=keyword, enabled=enabled)
    return ok(PageData(items=[_summary(row) for row in rows], total=total, page=page, page_size=page_size))


@router.post("", response_model=ApiResponse[AgentSummary], status_code=201)
async def create_agent(request: AgentUpsert) -> ApiResponse[AgentSummary]:
    row = await _repo().create(**request.model_dump(), request_id=request_id_ctx.get())
    return ok(_summary(row), message="created")


@router.put("/{agent_id}", response_model=ApiResponse[AgentSummary])
async def save_agent(agent_id: UUID, request: AgentSave) -> ApiResponse[AgentSummary]:
    row = await _repo().save(
        agent_id,
        **request.model_dump(exclude={"revision"}),
        expected_revision=request.revision,
        request_id=request_id_ctx.get(),
    )
    return ok(_summary(row))


@router.post("/{agent_id}/enable", response_model=ApiResponse[dict[str, bool]])
async def enable_agent(agent_id: UUID) -> ApiResponse[dict[str, bool]]:
    await _repo().set_enabled(agent_id, enabled=True, request_id=request_id_ctx.get())
    return ok({"enabled": True})


@router.post("/{agent_id}/disable", response_model=ApiResponse[dict[str, bool]])
async def disable_agent(agent_id: UUID) -> ApiResponse[dict[str, bool]]:
    await _repo().set_enabled(agent_id, enabled=False, request_id=request_id_ctx.get())
    return ok({"enabled": False})


@router.delete("/{agent_id}", response_model=ApiResponse[dict[str, bool]])
async def delete_agent(agent_id: UUID) -> ApiResponse[dict[str, bool]]:
    await _repo().soft_delete(agent_id, request_id=request_id_ctx.get())
    return ok({"deleted": True})
