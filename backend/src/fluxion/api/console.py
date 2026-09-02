from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Header, Query
from fastapi.responses import JSONResponse

from fluxion.api.admin_users import register_admin_user_routes
from fluxion.api.console_errors import _register_error_handlers
from fluxion.api.console_helpers import _actor, _kind, _publication_response
from fluxion.api.console_models import (
    DeprecatePayload,
    PublishPayload,
    ResourceCreatePayload,
    ResourceUpdatePayload,
    RollbackPayload,
    WorkflowValidatePayload,
)
from fluxion.api.console_routes_governance import register_console_governance_routes
from fluxion.api.console_routes_read import (
    _register_p1_routes,
    _register_read_side_routes,
    _register_trace_routes,
)
from fluxion.api.middleware import RequestContextMiddleware
from fluxion.api.operations import register_operations_routes
from fluxion.api.responses import success
from fluxion.api.studio import register_studio_routes
from fluxion.api.workflow import register_workflow_projection_routes
from fluxion.config import DevModeSettings
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.console_contracts import (
    CreateResourceDraftRequest,
    DeprecateResourceVersionRequest,
    PublishResourceVersionRequest,
    ReleaseGateRequest,
    RollbackResourceRequest,
    UpdateResourceDraftRequest,
)
from fluxion.services.console_payloads import resource_payload
from fluxion.services.operations_app import OperationsApplicationService
from fluxion.services.runtime_app import RuntimeApplicationService
from fluxion.services.workflow_projection import WorkflowProjectionService
from fluxion.users import UserDomainService


def create_app(
    service: ConsoleApplicationService,
    *,
    dev_mode: DevModeSettings | None = None,
    runtime_service: RuntimeApplicationService | None = None,
    user_service: UserDomainService | None = None,
    projection_service: WorkflowProjectionService | None = None,
    operations_service: OperationsApplicationService | None = None,
) -> FastAPI:
    app = FastAPI(title="Fluxion Console API")
    app.add_middleware(RequestContextMiddleware, dev_mode=dev_mode)
    _register_error_handlers(app)
    _register_health_routes(app)
    register_studio_routes(app, service, runtime_service=runtime_service)
    register_admin_user_routes(app, service, user_service=user_service)
    register_operations_routes(app, operations_service)
    _register_create_resource_route(app, service)
    _register_list_resources_route(app, service)
    _register_resource_schema_route(app, service)
    _register_get_resource_route(app, service)
    _register_list_versions_route(app, service)
    _register_update_resource_route(app, service)
    _register_validate_resource_route(app, service)
    _register_validate_publish_route(app, service)
    _register_test_connection_route(app, service)
    _register_publish_resource_route(app, service)
    _register_rollback_resource_route(app, service)
    _register_deprecate_resource_route(app, service)
    register_console_governance_routes(app, service)
    _register_p1_routes(app, service)
    _register_read_side_routes(app, service)
    _register_trace_routes(app, service)
    if projection_service is not None:
        register_workflow_projection_routes(app, projection_service=projection_service)
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


def _register_resource_schema_route(app: FastAPI, service: ConsoleApplicationService) -> None:
    # ADR-012：spec model 是表单单一真相源——前端按 schema 自渲染 Semi 表单，
    # 用户不再手写 JSON。必须注册在 /{resource_type}/{resource_id} 之前，
    # 否则 "schema" 会被当成 resource_id 吞掉。
    @app.get("/api/v1/resources/{resource_type}/schema")
    async def get_resource_schema(
        resource_type: str,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        schema = await service.resource_schema(_kind(resource_type))
        return success(schema)


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

    @app.post("/api/v1/resources/{resource_type}/{resource_id}:working-draft")
    async def ensure_working_draft(
        resource_type: str,
        resource_id: str,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        # remediation §14.3/§25：编辑已发布资源自动创建/复用 working draft，
        # 用户无感，无需显式「创建草稿」。
        actor = _actor(x_actor_id)
        working = await service.ensure_working_draft(
            actor,
            _kind(resource_type),
            resource_id,
        )
        return success(resource_payload(working))


def _register_test_connection_route(
    app: FastAPI,
    service: ConsoleApplicationService,
) -> None:
    @app.post("/api/v1/model-providers/{provider_id}:test-connection")
    async def test_connection(
        provider_id: str,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        # TASK-019：Provider 连接测试（可达性 + 发现模型），凭据/端点错误可操作。
        actor = _actor(x_actor_id)
        result = await service.test_model_provider_connection(
            actor,
            provider_id,
        )
        return success(
            {
                "reachable": result.reachable,
                "discovered_models": result.discovered_models,
                "error": result.error,
            }
        )

    @app.post("/api/v1/mcp-servers/{mcp_id}:test-connection")
    async def test_mcp_connection(
        mcp_id: str,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        # B-S-07（TASK-019 返工）：MCP 连接测试（握手 + 发现工具）。
        actor = _actor(x_actor_id)
        result = await service.test_mcp_connection(actor, mcp_id)
        return success(
            {
                "reachable": result.reachable,
                "discovered_tools": result.discovered_tools,
                "error": result.error,
            }
        )


def _register_validate_publish_route(
    app: FastAPI,
    service: ConsoleApplicationService,
) -> None:
    @app.post("/api/v1/resources/{resource_type}/{resource_id}/versions/{version}:validate-publish")
    async def validate_publish(
        resource_type: str,
        resource_id: str,
        version: str,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        # remediation §14.4：发布前完整校验，返回可操作问题清单。
        actor = _actor(x_actor_id)
        result = await service.validate_publish(
            actor,
            _kind(resource_type),
            resource_id,
            version,
        )
        return success(
            {"valid": result.valid, "issues": result.issues}
        )


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
        gate_request = (
            ReleaseGateRequest(
                candidate_eval_run_id=payload.gate.candidate_eval_run_id,
                baseline_eval_run_id=payload.gate.baseline_eval_run_id,
                threshold=payload.gate.threshold,
            )
            if payload.gate is not None
            else None
        )
        result = await service.publish_resource_version(
            actor,
            PublishResourceVersionRequest(
                tenant_id=actor.tenant_id,
                kind=_kind(resource_type),
                resource_id=resource_id,
                version=version,
                expected_base_version=payload.expected_base_version,
                publish_note=payload.publish_note,
                gate=gate_request,
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
