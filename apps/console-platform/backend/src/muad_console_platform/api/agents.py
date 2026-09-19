import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from muad_api import ApiResponse, ok, paginate
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.agent_mcp_service import AgentMcpService
from ..application.agent_service import AgentService
from ..application.grant_service import GrantService
from ..application.dto import AgentBindSkillRequest as BindSkillRequest
from ..application.audit_service import AuditActor
from ..application.dto import AgentCreateRequest, AgentDetail, AgentListItem, AgentUpdateRequest
from ..application.skill_service import SkillService
from ..infrastructure.db import get_session
from ..infrastructure.models.control import AgentDefinition
from .deps import CurrentAccount, get_source_ip, get_tenant_id

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


def _detail(agent: AgentDefinition) -> dict[str, Any]:
    return AgentDetail.model_validate(agent).model_dump(mode="json")


def _item(agent: AgentDefinition) -> dict[str, Any]:
    return AgentListItem.model_validate(agent).model_dump(mode="json")


def _actor(account: CurrentAccount, request: Request) -> AuditActor:
    return AuditActor(account_id=account.id, source_ip=get_source_ip(request))


@router.get("")
async def list_agents(
    request: Request,
    tenant_id: TenantId,
    session: Session,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
) -> ApiResponse[Any]:
    items, total = await AgentService(session).list_agents(
        tenant_id, page, page_size, keyword=keyword, enabled=enabled
    )
    return ok(
        request.app.state.message_catalog,
        paginate(items=items, page=page, page_size=page_size, total=total),
    )


@router.post("")
async def create_agent(
    payload: AgentCreateRequest,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
) -> ApiResponse[Any]:
    agent = await AgentService(session).create_agent(
        tenant_id, payload, _actor(account, request), idempotency_key=idempotency_key
    )
    return ok(request.app.state.message_catalog, _detail(agent))


@router.get("/{agent_id}")
async def get_agent(
    agent_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    service = AgentService(session)
    agent = await service.get_agent(tenant_id, agent_id)
    detail = _detail(agent)
    detail.update(await service.agent_counts(tenant_id, agent.id))
    return ok(request.app.state.message_catalog, detail)


@router.put("/{agent_id}")
async def update_agent(
    agent_id: uuid.UUID,
    payload: AgentUpdateRequest,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    data = await AgentService(session).update_agent(
        tenant_id,
        agent_id,
        payload,
        _actor(account, request),
    )
    return ok(request.app.state.message_catalog, data)


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    await AgentService(session).delete_agent(tenant_id, agent_id, _actor(account, request))
    return ok(request.app.state.message_catalog, {"deleted": True})


@router.get("/{agent_id}/skills")
async def list_agent_skills(
    agent_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[Any]:
    items, total = await SkillService(session).list_agent_skills(
        tenant_id, agent_id, page, page_size
    )
    return ok(
        request.app.state.message_catalog,
        paginate(
            items=[item.model_dump(mode="json") for item in items],
            page=page,
            page_size=page_size,
            total=total,
        ),
    )


@router.post("/{agent_id}/skills/{skill_id}")
async def bind_agent_skill(
    agent_id: uuid.UUID,
    skill_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
    payload: BindSkillRequest | None = None,
) -> ApiResponse[Any]:
    item = await SkillService(session).bind_skill(
        tenant_id,
        agent_id,
        skill_id,
        _actor(account, request),
        sort_order=payload.sort_order if payload else 0,
    )
    return ok(request.app.state.message_catalog, item.model_dump(mode="json"))


@router.delete("/{agent_id}/skills/{skill_id}")
async def unbind_agent_skill(
    agent_id: uuid.UUID,
    skill_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    data = await SkillService(session).unbind_skill(
        tenant_id,
        agent_id,
        skill_id,
        _actor(account, request),
    )
    return ok(request.app.state.message_catalog, data)


@router.get("/{agent_id}/mcp-servers")
async def list_agent_mcp_servers(
    agent_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[Any]:
    items, total = await AgentMcpService(session).list_bindings(
        tenant_id, agent_id, page, page_size
    )
    return ok(
        request.app.state.message_catalog,
        paginate(items=items, page=page, page_size=page_size, total=total),
    )


@router.post("/{agent_id}/mcp-servers/{mcp_id}")
async def bind_agent_mcp(
    agent_id: uuid.UUID,
    mcp_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    data = await AgentMcpService(session).bind(
        tenant_id, agent_id, mcp_id, _actor(account, request)
    )
    return ok(request.app.state.message_catalog, data)


@router.delete("/{agent_id}/mcp-servers/{mcp_id}")
async def unbind_agent_mcp(
    agent_id: uuid.UUID,
    mcp_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    data = await AgentMcpService(session).unbind(
        tenant_id, agent_id, mcp_id, _actor(account, request)
    )
    return ok(request.app.state.message_catalog, data)


@router.get("/{agent_id}/users")
async def list_agent_users(
    agent_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[Any]:
    items, total = await GrantService(session).list_by_agent(
        tenant_id, agent_id, keyword, page, page_size
    )
    return ok(
        request.app.state.message_catalog,
        paginate(
            items=[
                {
                    **item,
                    "user_id": str(item["user_id"]),
                    "granted_by": str(item["granted_by"]),
                    "granted_at": item["granted_at"].isoformat(),
                    "create_time": item["create_time"].isoformat(),
                }
                for item in items
            ],
            page=page,
            page_size=page_size,
            total=total,
        ),
    )


@router.post("/{agent_id}/users/{user_id}")
async def grant_agent_user(
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    service = GrantService(session)
    grant = await service.grant(
        tenant_id,
        user_id,
        agent_id,
        account.id,
        _actor(account, request),
    )
    return ok(
        request.app.state.message_catalog,
        {
            "agent_id": str(agent_id),
            "user_id": str(user_id),
            "granted_by": str(grant.granted_by),
            "granted_at": grant.granted_at.isoformat(),
        },
    )


@router.delete("/{agent_id}/users/{user_id}")
async def revoke_agent_user(
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    account: CurrentAccount,
    tenant_id: TenantId,
    session: Session,
) -> ApiResponse[Any]:
    await GrantService(session).revoke(
        tenant_id,
        user_id,
        agent_id,
        _actor(account, request),
    )
    return ok(
        request.app.state.message_catalog,
        {"agent_id": str(agent_id), "user_id": str(user_id), "is_deleted": True},
    )
