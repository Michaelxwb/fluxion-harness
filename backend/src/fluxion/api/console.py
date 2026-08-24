from __future__ import annotations

import traceback
from typing import Annotated

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from fluxion.api.middleware import RequestContextMiddleware
from fluxion.api.responses import failure, success
from fluxion.config import DevModeSettings
from fluxion.errors.console import (
    FORBIDDEN,
    INTERNAL_ERROR,
    RESOURCE_NOT_FOUND,
    VALIDATION_FAILED,
    ConsoleError,
)
from fluxion.observability.context import current_context
from fluxion.observability.logging import emit_error_log
from fluxion.resources import ResourceKind, ResourceVisibility
from fluxion.services.console_app import (
    ConsoleApplicationService,
    approval_payload,
    audit_payload,
    binding_payload,
    credential_payload,
    issued_chat_access_payload,
    platform_user_payload,
    policy_payload,
    publish_payload,
    resource_payload,
    run_payload,
    trace_payload,
)
from fluxion.services.console_contracts import (
    ConsoleActor,
    CreateApprovalRequest,
    CreateBindingRequest,
    CreateResourceDraftRequest,
    DecideApprovalRequest,
    DeprecateResourceVersionRequest,
    PublishResourceResult,
    PublishResourceVersionRequest,
    RollbackResourceRequest,
    UpdateResourceDraftRequest,
)


class ResourceCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str | None = None
    resource_id: str
    version: str
    spec: dict[str, object]
    visibility: ResourceVisibility = ResourceVisibility.PRIVATE


class ResourceUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: dict[str, object]


class PublishPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publish_note: str | None = None
    expected_base_version: str | None = None


class RollbackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_version: str
    force: bool = False
    approval_id: str | None = None


class DeprecatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class BindingCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_type: str
    subject_id: str
    resource_type: ResourceKind
    resource_id: str
    version_selector: str = "latest-published"
    credential_ref: str | None = None
    config: dict[str, object] = Field(default_factory=dict)


class WorkflowValidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlatformUserCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform_user_id: str
    display_name: str = ""


class ChatAccessCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_profile_id: str


class ApprovalCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_type: str
    resource_id: str
    target_version: str
    reason: str | None = None
    ttl_seconds: float = 3600.0


class ApprovalDecidePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    reason: str | None = None


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


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ConsoleError)
    async def console_error_handler(request: Request, exc: ConsoleError) -> JSONResponse:
        return failure(exc.code, exc.message, status_code=exc.status_code, request=request)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        del exc
        return failure(VALIDATION_FAILED, "validation failed", status_code=400, request=request)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        # 路由级 404/405 等 HTTPException 需回到统一 envelope，而不是落到
        # 通用 Exception handler 变成 500 INTERNAL_ERROR。
        if exc.status_code == 404:
            return failure(RESOURCE_NOT_FOUND, "not found", status_code=404, request=request)
        if exc.status_code == 403:
            return failure(FORBIDDEN, "forbidden", status_code=403, request=request)
        return failure(
            VALIDATION_FAILED,
            str(exc.detail),
            status_code=exc.status_code,
            request=request,
        )

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
        _emit_unhandled_error_log(request, exc)
        return failure(INTERNAL_ERROR, "internal error", status_code=500, request=request)


def _emit_unhandled_error_log(request: Request, exc: Exception) -> None:
    emit_error_log(
        request_id=_state_or_header(request, "request_id", "X-Request-ID"),
        trace_id=_state_or_header(request, "trace_id", "X-Trace-ID"),
        tenant_id=_state_or_unknown(request, "tenant_id"),
        actor_id=_state_or_unknown(request, "actor_id"),
        method=request.method,
        route=request.url.path,
        error_type=type(exc).__name__,
        error_code=INTERNAL_ERROR,
        stack=traceback.format_exc(),
    )


def _state_or_header(request: Request, state_key: str, header_name: str) -> str:
    value = getattr(request.state, state_key, None)
    if isinstance(value, str) and value:
        return value
    return request.headers.get(header_name, "unknown")


def _state_or_unknown(request: Request, state_key: str) -> str:
    value = getattr(request.state, state_key, None)
    return value if isinstance(value, str) and value else "unknown"


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
    @app.get("/api/v1/resources/{resource_type}")
    async def list_resources(
        resource_type: str,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        resources, total = await service.list_resources(
            _actor(x_actor_id),
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
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> JSONResponse:
        bindings, total = await service.list_bindings(
            _actor(None),
            page=page,
            page_size=page_size,
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


def _register_p1_routes(app: FastAPI, service: ConsoleApplicationService) -> None:
    @app.get("/api/v1/policies")
    async def list_policies(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> JSONResponse:
        items, total = await service.list_policies(
            _actor(None), page=page, page_size=page_size
        )
        return success(_page([policy_payload(item) for item in items], page, page_size, total))

    @app.get("/api/v1/capabilities")
    async def list_capabilities() -> JSONResponse:
        items = await service.list_capabilities(_actor(None))
        return success({"items": items, "total": len(items)})

    @app.get("/api/v1/runtime-status")
    async def runtime_status() -> JSONResponse:
        return success(await service.runtime_status(_actor(None)))


def _register_trace_routes(app: FastAPI, service: ConsoleApplicationService) -> None:
    @app.get("/api/v1/traces/{trace_id}")
    async def get_trace(trace_id: str) -> JSONResponse:
        return success(trace_payload(await service.get_trace(_actor(None), trace_id)))


def _register_read_side_routes(app: FastAPI, service: ConsoleApplicationService) -> None:
    @app.get("/api/v1/credentials")
    async def list_credentials(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> JSONResponse:
        items, total = await service.list_credentials(
            _actor(None), page=page, page_size=page_size
        )
        return success(_page([credential_payload(item) for item in items], page, page_size, total))

    @app.get("/api/v1/runs")
    async def list_runs(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> JSONResponse:
        items, total = await service.list_runs(_actor(None), page=page, page_size=page_size)
        return success(_page([run_payload(item) for item in items], page, page_size, total))

    @app.get("/api/v1/runs/{execution_id}")
    async def get_run(execution_id: str) -> JSONResponse:
        return success(run_payload(await service.get_run(_actor(None), execution_id)))

    @app.get("/api/v1/audit")
    async def list_audit(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> JSONResponse:
        items, total = await service.list_audit(_actor(None), page=page, page_size=page_size)
        return success(_page([audit_payload(item) for item in items], page, page_size, total))


def _page(items: list[dict[str, object]], page: int, page_size: int, total: int) -> dict[str, object]:
    return {"items": items, "page": page, "page_size": page_size, "total": total}


def _publication_response(result: PublishResourceResult) -> JSONResponse:
    response = success(publish_payload(result))
    response.headers["X-Publish-ID"] = result.publish_id
    return response


def _actor(actor_id: str | None) -> ConsoleActor:
    context = current_context()
    if context is None:
        return ConsoleActor(
            tenant_id="unknown",
            actor_id=actor_id or "unknown",
            request_id="req_unknown",
            trace_id="trace_unknown",
        )
    return ConsoleActor(
        tenant_id=context.tenant_id,
        actor_id=context.actor_id,
        request_id=context.request_id,
        trace_id=context.trace_id,
    )


def _kind(value: str) -> ResourceKind:
    try:
        return ResourceKind(value)
    except ValueError as exc:
        raise ConsoleError(VALIDATION_FAILED, "invalid resource type", 400) from exc
