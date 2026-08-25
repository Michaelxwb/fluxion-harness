from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Header, Query
from fastapi.responses import JSONResponse

from fluxion.api.console_errors import _register_error_handlers
from fluxion.api.console_helpers import _actor, _kind, _publication_response
from fluxion.api.console_models import (
    ApprovalCreatePayload,
    ApprovalDecidePayload,
    BindingCreatePayload,
    ChatAccessCreatePayload,
    DeprecatePayload,
    PlatformUserCreatePayload,
    PublishPayload,
    ResourceCreatePayload,
    ResourceUpdatePayload,
    RollbackPayload,
    WorkflowValidatePayload,
)
from fluxion.api.console_routes_read import (
    _register_p1_routes,
    _register_read_side_routes,
    _register_trace_routes,
)
from fluxion.api.middleware import RequestContextMiddleware
from fluxion.api.responses import success
from fluxion.config import DevModeSettings
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.console_contracts import (
    CreateApprovalRequest,
    CreateBindingRequest,
    CreateResourceDraftRequest,
    DecideApprovalRequest,
    DeprecateResourceVersionRequest,
    PublishResourceVersionRequest,
    RollbackResourceRequest,
    UpdateResourceDraftRequest,
)
from fluxion.services.console_payloads import (
    approval_payload,
    binding_payload,
    issued_chat_access_payload,
    platform_user_payload,
    resource_payload,
)


def create_app(
    service: ConsoleApplicationService,
    *,
    dev_mode: DevModeSettings | None = None,
) -> FastAPI:
    app = FastAPI(title="Fluxion Console API")
    app.add_middleware(RequestContextMiddleware, dev_mode=dev_mode)
    _register_error_handlers(app)
    _register_health_routes(app)
    _register_create_resource_route(app, service)
    _register_list_resources_route(app, service)
    _register_get_resource_route(app, service)
    _register_list_versions_route(app, service)
    _register_update_resource_route(app, service)
    _register_validate_resource_route(app, service)
    _register_publish_resource_route(app, service)
    _register_rollback_resource_route(app, service)
    _register_deprecate_resource_route(app, service)
    _register_approval_routes(app, service)
    _register_binding_routes(app, service)
    _register_platform_user_routes(app, service)
    _register_p1_routes(app, service)
    _register_read_side_routes(app, service)
    _register_trace_routes(app, service)
    return app


def _register_health_routes(app: FastAPI) -> None:
    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return success({"status": "ok"})


def _register_create_resource_route(app: FastAPI, service: ConsoleApplicationService) -> None:
    @app.post("/api/v1/resources/{resource_type}")
    async def create_resource(
        resource_type: str,
        payload: ResourceCreatePayload,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        actor = _actor(x_actor_id)
        created = await service.create_resource_draft(
            actor,
            CreateResourceDraftRequest(
                tenant_id=payload.tenant_id or actor.tenant_id,
                kind=_kind(resource_type),
                resource_id=payload.resource_id,
                version=payload.version,
                visibility=payload.visibility,
                spec=dict(payload.spec),
            ),
        )
        return success(resource_payload(created))


def _register_list_resources_route(app: FastAPI, service: ConsoleApplicationService) -> None:
    # 资源中心一次列出租户下全部类型的资源：GET /api/v1/resources（可带 resource_type 过滤），
    # 后端是单表 resource_definitions，不再需要前端并发多个按类型接口再合并。
    @app.get("/api/v1/resources")
    async def list_resources(
        resource_type: Annotated[str | None, Query()] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        actor = _actor(x_actor_id)
        if resource_type is None:
            resources, total = await service.list_all_resources(
                actor,
                page=page,
                page_size=page_size,
            )
        else:
            resources, total = await service.list_resources(
                actor,
                _kind(resource_type),
                page=page,
                page_size=page_size,
            )
        return success(
            {
                "items": [resource_payload(resource) for resource in resources],
                "page": page,
                "page_size": page_size,
                "total": total,
            }
        )


def _register_get_resource_route(app: FastAPI, service: ConsoleApplicationService) -> None:
    @app.get("/api/v1/resources/{resource_type}/{resource_id}")
    async def get_resource(
        resource_type: str,
        resource_id: str,
        version: str | None = None,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        resource = await service.get_resource(
            _actor(x_actor_id),
            _kind(resource_type),
            resource_id,
            version=version,
        )
        return success(resource_payload(resource))


def _register_list_versions_route(app: FastAPI, service: ConsoleApplicationService) -> None:
    @app.get("/api/v1/resources/{resource_type}/{resource_id}/versions")
    async def list_versions(
        resource_type: str,
        resource_id: str,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        resources, total = await service.list_resource_versions(
            _actor(x_actor_id),
            _kind(resource_type),
            resource_id,
            page=page,
            page_size=page_size,
        )
        return success(
            {
                "items": [resource_payload(resource) for resource in resources],
                "page": page,
                "page_size": page_size,
                "total": total,
            }
        )


def _register_update_resource_route(app: FastAPI, service: ConsoleApplicationService) -> None:
    @app.put("/api/v1/resources/{resource_type}/{resource_id}/versions/{version}")
    async def update_resource(
        resource_type: str,
        resource_id: str,
        version: str,
        payload: ResourceUpdatePayload,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        actor = _actor(x_actor_id)
        updated = await service.update_resource_draft(
            actor,
            UpdateResourceDraftRequest(
                tenant_id=actor.tenant_id,
                kind=_kind(resource_type),
                resource_id=resource_id,
                version=version,
                spec=dict(payload.spec),
            ),
        )
        return success(resource_payload(updated))


def _register_validate_resource_route(
    app: FastAPI,
    service: ConsoleApplicationService,
) -> None:
    @app.post("/api/v1/resources/{resource_type}/{resource_id}/versions/{version}:validate")
    async def validate_resource(
        resource_type: str,
        resource_id: str,
        version: str,
        payload: WorkflowValidatePayload,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        del payload
        result = await service.validate_resource_version(
            _actor(x_actor_id),
            _kind(resource_type),
            resource_id,
            version,
        )
        return success({"diagnostics": list(result.diagnostics), "valid": result.valid})


def _register_publish_resource_route(app: FastAPI, service: ConsoleApplicationService) -> None:
    @app.post("/api/v1/resources/{resource_type}/{resource_id}/versions/{version}:publish")
    async def publish_resource(
        resource_type: str,
        resource_id: str,
        version: str,
        payload: PublishPayload,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        actor = _actor(x_actor_id)
        result = await service.publish_resource_version(
            actor,
            PublishResourceVersionRequest(
                tenant_id=actor.tenant_id,
                kind=_kind(resource_type),
                resource_id=resource_id,
                version=version,
                expected_base_version=payload.expected_base_version,
                publish_note=payload.publish_note,
            ),
        )
        return _publication_response(result)


def _register_rollback_resource_route(
    app: FastAPI,
    service: ConsoleApplicationService,
) -> None:
    @app.post("/api/v1/resources/{resource_type}/{resource_id}:rollback")
    async def rollback_resource(
        resource_type: str,
        resource_id: str,
        payload: RollbackPayload,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        actor = _actor(x_actor_id)
        result = await service.rollback_resource(
            actor,
            RollbackResourceRequest(
                tenant_id=actor.tenant_id,
                kind=_kind(resource_type),
                resource_id=resource_id,
                target_version=payload.target_version,
                force=payload.force,
                approval_id=payload.approval_id,
            ),
        )
        return _publication_response(result)


def _register_deprecate_resource_route(
    app: FastAPI,
    service: ConsoleApplicationService,
) -> None:
    @app.post("/api/v1/resources/{resource_type}/{resource_id}/versions/{version}:deprecate")
    async def deprecate_resource(
        resource_type: str,
        resource_id: str,
        version: str,
        payload: DeprecatePayload,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        actor = _actor(x_actor_id)
        result = await service.deprecate_resource_version(
            actor,
            DeprecateResourceVersionRequest(
                tenant_id=actor.tenant_id,
                kind=_kind(resource_type),
                resource_id=resource_id,
                version=version,
                reason=payload.reason,
            ),
        )
        return _publication_response(result)


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
            runtime_profile_id=payload.runtime_profile_id,
        )
        return success(issued_chat_access_payload(issued))

    @app.post("/api/v1/chat-access/{access_id}:revoke")
    async def revoke_chat_access(access_id: str) -> JSONResponse:
        record = await service.revoke_chat_access(_actor(None), access_id=access_id)
        return success({"access_id": record.access_id, "status": "revoked"})
