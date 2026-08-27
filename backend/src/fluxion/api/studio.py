"""Product API（Studio）：前端 BFF 的业务语义资源端点（TASK-004）。

- 统一 envelope / request_id 由既有 responses + middleware 基础设施承担；
  业务 handler 不手写响应结构。
- 与 Control API（/api/v1/resources/*）共用同一 service 层与治理发布路径，
  仅在形态上收敛为产品语义 kind 别名 + 前置 typed 校验（E-01）。
"""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Header, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from fluxion.agents.definitions import AgentDefinition
from fluxion.api.console_helpers import _actor, _publication_response
from fluxion.api.responses import success
from fluxion.errors.console import RUNTIME_APPLICATION_ERROR, VALIDATION_FAILED, ConsoleError
from fluxion.resources import ResourceKind, ResourceVisibility
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.console_contracts import (
    CreateResourceDraftRequest,
    PublishResourceVersionRequest,
)
from fluxion.services.console_payloads import resource_payload

# 产品语义 kind 别名 → Registry kind。白名单即契约：IA 不随 Resource 自动增长。
_STUDIO_KINDS: dict[str, ResourceKind] = {
    "agents": ResourceKind.AGENT_DEFINITION,
    "models": ResourceKind.MODEL,
    "tools": ResourceKind.TOOL,
    "skills": ResourceKind.SKILL,
    "mcp": ResourceKind.MCP,
    "runtime-profiles": ResourceKind.RUNTIME_PROFILE,
    "secrets": ResourceKind.SECRET,
    "policies": ResourceKind.POLICY,
    "evals": ResourceKind.EVAL_SET,
}


class StudioCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    resource_id: str | None = None
    version: str = "1"
    visibility: ResourceVisibility = ResourceVisibility.PRIVATE
    spec: dict[str, object] = Field(default_factory=dict)


class TestRunPayload(BaseModel):
    # 轮数上限不在此暴露：由所引用 RuntimeProfile.max_rounds（mechanics）决定，
    # 保持"轮数预算属运行配置而非单次请求可改"的契约边界（TASK-A104）。
    model_config = ConfigDict(extra="forbid")

    input: str


def _studio_kind(value: str) -> ResourceKind:
    try:
        return _STUDIO_KINDS[value]
    except KeyError as exc:
        raise ConsoleError(
            VALIDATION_FAILED, f"unsupported studio resource type: {value}", 400
        ) from exc


def register_studio_routes(
    app: FastAPI,
    service: ConsoleApplicationService,
    *,
    runtime_service: object | None = None,
) -> None:
    @app.post("/studio/{kind}")
    async def create_studio_resource(
        kind: str,
        payload: StudioCreatePayload,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        actor = _actor(x_actor_id)
        registry_kind = _studio_kind(kind)
        # E-01：前置 typed 校验，字段定位在进入 draft 前。
        service.validate_spec_shape(registry_kind, payload.spec)
        created = await service.create_resource_draft(
            actor,
            CreateResourceDraftRequest(
                tenant_id=actor.tenant_id,
                kind=registry_kind,
                resource_id=payload.resource_id or f"{kind.rstrip('s')}_{uuid4().hex[:12]}",
                version=payload.version,
                visibility=payload.visibility,
                spec=dict(payload.spec),
            ),
        )
        return success(resource_payload(created))

    @app.get("/studio/{kind}")
    async def list_studio_resources(
        kind: str,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        actor = _actor(x_actor_id)
        resources, total = await service.list_resources(
            actor,
            _studio_kind(kind),
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

    @app.get("/studio/{kind}/{resource_id}")
    async def get_studio_resource(
        kind: str,
        resource_id: str,
        version: Annotated[str | None, Query()] = None,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        actor = _actor(x_actor_id)
        resource = await service.get_resource(actor, _studio_kind(kind), resource_id, version=version)
        return success(resource_payload(resource))

    @app.post("/studio/{kind}/{resource_id}/versions/{version}:publish")
    async def publish_studio_resource(
        kind: str,
        resource_id: str,
        version: str,
        publish_note: Annotated[str | None, Query()] = None,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        actor = _actor(x_actor_id)
        result = await service.publish_resource_version(
            actor,
            PublishResourceVersionRequest(
                tenant_id=actor.tenant_id,
                kind=_studio_kind(kind),
                resource_id=resource_id,
                version=version,
                expected_base_version=None,
                publish_note=publish_note,
            ),
        )
        return _publication_response(result)

    @app.post("/studio/agents/{agent_id}/test-run", response_model=None)
    async def test_run_agent(
        agent_id: str,
        payload: TestRunPayload,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse | StreamingResponse:
        # TASK-005：Agent Studio 试跑。执行链复用 RuntimeApplicationService
        # （failover/retry/deadline 有界 + 结构化脱敏日志）；Console 单独部署时
        # 未装配 runtime → 显式 503，不静默。
        from fluxion.api.runtime import _sse_events

        if runtime_service is None:
            raise ConsoleError(
                RUNTIME_APPLICATION_ERROR,
                "studio test-run requires runtime service",
                503,
            )
        actor = _actor(x_actor_id)
        definition = await service.get_resource(
            actor, ResourceKind.AGENT_DEFINITION, agent_id
        )
        agent_spec = AgentDefinition.model_validate(definition.spec_json)
        ref = agent_spec.runtime_profile_ref
        from uuid import uuid4 as _uuid4

        from fluxion.services.runtime_contracts import RunRuntimeRequest

        request = RunRuntimeRequest(
            tenant_id=actor.tenant_id,
            user_id=f"studio-test:{actor.actor_id}",
            runtime_profile_id=ref.id if ref is not None else agent_id,
            session_id=f"test-run-{_uuid4().hex[:12]}",
            input_message=payload.input,
            runtime_profile_version_selector=(
                ref.version if ref is not None else "latest-published"
            ),
            agent_definition_id=agent_id,
        )
        events = _sse_events(runtime_service, request)  # type: ignore[arg-type]
        return StreamingResponse(events, media_type="text/event-stream")
