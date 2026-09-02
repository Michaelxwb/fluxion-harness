from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Header, Query
from fastapi.responses import JSONResponse

from fluxion.api.console_helpers import _actor, _kind
from fluxion.api.console_models import (
    ApprovalCreatePayload,
    ApprovalDecidePayload,
    BindingCreatePayload,
    ChatAccessCreatePayload,
    PlatformUserCreatePayload,
)
from fluxion.api.responses import success
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.console_contracts import (
    CreateApprovalRequest,
    CreateBindingRequest,
    DecideApprovalRequest,
)
from fluxion.services.console_payloads import (
    approval_payload,
    binding_payload,
    issued_chat_access_payload,
    platform_user_payload,
)


def register_console_governance_routes(
    app: FastAPI,
    service: ConsoleApplicationService,
) -> None:
    _register_approval_routes(app, service)
    _register_binding_routes(app, service)
    _register_platform_user_routes(app, service)


def _register_approval_routes(app: FastAPI, service: ConsoleApplicationService) -> None:
    @app.post("/api/v1/approvals")
    async def create_approval(
        payload: ApprovalCreatePayload,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        actor = _actor(x_actor_id)
        record = await service.create_approval(
            actor,
            CreateApprovalRequest(
                tenant_id=actor.tenant_id,
                kind=_kind(payload.resource_type),
                resource_id=payload.resource_id,
                target_version=payload.target_version,
                reason=payload.reason,
                ttl_seconds=payload.ttl_seconds,
            ),
        )
        return success(approval_payload(record))

    @app.post("/api/v1/approvals/{approval_id}:decide")
    async def decide_approval(
        approval_id: str,
        payload: ApprovalDecidePayload,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        actor = _actor(x_actor_id)
        record = await service.decide_approval(
            actor,
            DecideApprovalRequest(
                tenant_id=actor.tenant_id,
                approval_id=approval_id,
                approved=payload.approved,
                reason=payload.reason,
            ),
        )
        return success(approval_payload(record))


def _register_binding_routes(app: FastAPI, service: ConsoleApplicationService) -> None:
    @app.get("/api/v1/bindings")
    async def list_bindings(
        resource_type: Annotated[str | None, Query()] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> JSONResponse:
        bindings, total = await service.list_bindings(
            _actor(None),
            page=page,
            page_size=page_size,
            resource_type=_kind(resource_type) if resource_type is not None else None,
        )
        return success(
            {
                "items": [binding_payload(binding) for binding in bindings],
                "page": page,
                "page_size": page_size,
                "total": total,
            }
        )

    @app.post("/api/v1/bindings")
    async def create_binding(
        payload: BindingCreatePayload,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        actor = _actor(x_actor_id)
        binding = await service.create_binding(
            actor,
            CreateBindingRequest(
                tenant_id=actor.tenant_id,
                subject_type=payload.subject_type,
                subject_id=payload.subject_id,
                resource_type=payload.resource_type,
                resource_id=payload.resource_id,
                version_selector=payload.version_selector,
                credential_ref=payload.credential_ref,
                config=dict(payload.config),
            ),
        )
        return success(binding_payload(binding))

    @app.post("/api/v1/bindings/{binding_id}:disable")
    async def disable_binding(binding_id: str) -> JSONResponse:
        await service.disable_binding(_actor(None), binding_id=binding_id)
        return success({"binding_id": binding_id, "status": "disabled"})


def _register_platform_user_routes(app: FastAPI, service: ConsoleApplicationService) -> None:
    @app.post("/api/v1/platform-users")
    async def create_platform_user(payload: PlatformUserCreatePayload) -> JSONResponse:
        user = await service.create_platform_user(
            _actor(None),
            platform_user_id=payload.platform_user_id,
            display_name=payload.display_name,
        )
        return success(platform_user_payload(user))

    @app.get("/api/v1/platform-users")
    async def list_platform_users(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> JSONResponse:
        users, total = await service.list_platform_users(
            _actor(None),
            page=page,
            page_size=page_size,
        )
        return success(
            {
                "items": [platform_user_payload(user) for user in users],
                "page": page,
                "page_size": page_size,
                "total": total,
            }
        )

    @app.post("/api/v1/platform-users/{platform_user_id}/chat-access")
    async def issue_chat_access(
        platform_user_id: str,
        payload: ChatAccessCreatePayload,
    ) -> JSONResponse:
        issued = await service.issue_chat_access(
            _actor(None),
            platform_user_id=platform_user_id,
            agent_id=payload.agent_id,
        )
        return success(issued_chat_access_payload(issued))

    @app.post("/api/v1/chat-access/{access_id}:revoke")
    async def revoke_chat_access(access_id: str) -> JSONResponse:
        record = await service.revoke_chat_access(_actor(None), access_id=access_id)
        return success({"access_id": record.access_id, "status": "revoked"})
