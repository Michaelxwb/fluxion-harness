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
    "model-providers": ResourceKind.MODEL_PROVIDER,
    "model-definitions": ResourceKind.MODEL_DEFINITION,
    "tools": ResourceKind.TOOL,
    "skills": ResourceKind.SKILL,
    "mcp": ResourceKind.MCP,
    "runtime-profiles": ResourceKind.RUNTIME_PROFILE,
    "secrets": ResourceKind.SECRET,
    "policies": ResourceKind.POLICY,
    "evals": ResourceKind.EVAL_SET,
    # TASK-007：workflows 创建收口到产品语义端点（此前缺口）
    "workflows": ResourceKind.WORKFLOW,
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

    @app.get("/studio/model-lab/projection")
    async def get_model_lab_projection() -> JSONResponse:
        """golden-path-closure TASK-025（§14 P2）：Model 页聚合投影。

        单查询（list_current_resources kind=None）返回租户全量当前版本资源，
        服务端过滤组装 Provider→Model 分组投影 + 凭据选项——消费方 ModelsPage
        由 3+3N 请求降为 1 请求（内部无 N+1）。
        """
        actor = _actor(None)
        all_resources, _total = await service.store.list_current_resources(
            None, tenant_id=actor.tenant_id, offset=0, limit=1000
        )
        providers: list[dict[str, object]] = []
        models: list[dict[str, object]] = []
        credentials: list[dict[str, object]] = []
        for definition in all_resources:
            spec = definition.spec_json if isinstance(definition.spec_json, dict) else {}
            if definition.kind is ResourceKind.MODEL_PROVIDER:
                providers.append(
                    {
                        "resource_id": definition.id,
                        # ProviderDefinition 用 display_name（TASK-025 修正：
                        # 连接 Modal 的「模型服务名称」存 spec.display_name）
                        "display_name": str(spec.get("display_name") or definition.id),
                        "version": definition.version,
                        "status": definition.status.value,
                        "base_url": spec.get("base_url", "-"),
                        "credential_ref": spec.get("credential_ref", ""),
                    }
                )
            elif definition.kind is ResourceKind.MODEL_DEFINITION:
                provider_ref = spec.get("provider_ref", {})
                if not isinstance(provider_ref, dict):
                    provider_ref = {}
                models.append(
                    {
                        "resource_id": definition.id,
                        "name": spec.get("name", definition.id),
                        "version": definition.version,
                        "provider_id": provider_ref.get("id", ""),
                    }
                )
            elif definition.kind is ResourceKind.SECRET:
                credentials.append(
                    {
                        "label": spec.get("name", definition.id),
                        "value": spec.get("secret_ref", ""),
                    }
                )
        return success(
            {
                "providers": providers,
                "models": models,
                "credentials": credentials,
            }
        )

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
        from fluxion.services.runtime_profile_resolution import (
            resolve_default_runtime_profile,
        )

        if ref is not None:
            profile_id: str = ref.id
            profile_version_selector: str = ref.version
        else:
            # ADR-A010：未配置 ref → 租户默认链（同名回退已废弃）
            default_profile = await resolve_default_runtime_profile(
                service.store, actor.tenant_id
            )
            if default_profile is None:
                raise ConsoleError(
                    RUNTIME_APPLICATION_ERROR,
                    "no default RuntimeProfile: tenant default and platform-default "
                    f"both missing for agent {agent_id} (ADR-A010)",
                    409,
                )
            profile_id = default_profile.id
            profile_version_selector = "latest-published"
        request = RunRuntimeRequest(
            tenant_id=actor.tenant_id,
            user_id=f"studio-test:{actor.actor_id}",
            runtime_profile_id=profile_id,
            session_id=f"test-run-{_uuid4().hex[:12]}",
            input_message=payload.input,
            runtime_profile_version_selector=profile_version_selector,
            agent_definition_id=agent_id,
        )
        events = _sse_events(runtime_service, request)  # type: ignore[arg-type]
        return StreamingResponse(events, media_type="text/event-stream")

    @app.get("/studio/agents/{agent_id}/dependencies")
    async def plan_agent_dependencies(agent_id: str) -> JSONResponse:
        """golden-path-closure TASK-024（§14 P2）：Capability 依赖规划。

        解析 Agent capabilities + workflow_ref → 依赖图（kind/id/version/可解析
        状态），供 Editor「依赖规划」呈现与发布前预检。
        """
        actor = _actor(None)
        definition = await service.get_resource(
            actor, ResourceKind.AGENT_DEFINITION, agent_id
        )
        agent_spec = AgentDefinition.model_validate(definition.spec_json)
        nodes: list[dict[str, object]] = []
        for binding in agent_spec.capabilities:
            resolved: ResourceDefinition | None = None
            try:
                resolved = await service.get_resource(
                    actor,
                    ResourceKind(binding.type.value),
                    binding.capability_ref,
                    version=binding.version_pin,
                )
            except Exception:  # noqa: BLE001 - 依赖不可解析属预期态（呈现 not_resolved）
                resolved = None
            nodes.append(
                {
                    "capability_ref": binding.capability_ref,
                    "kind": binding.type,
                    "version_pin": binding.version_pin,
                    "status": (
                        resolved.status.value
                        if resolved is not None
                        else "not_resolved"
                    ),
                }
            )
        workflow_status: str | None = None
        if agent_spec.workflow_ref is not None:
            workflow = await service.get_resource(
                actor, ResourceKind.WORKFLOW, agent_spec.workflow_ref.id
            )
            workflow_status = workflow.status.value if workflow is not None else "not_resolved"
        return success(
            {
                "agent_id": agent_id,
                "nodes": nodes,
                "workflow_ref": (
                    {"id": agent_spec.workflow_ref.id, "status": workflow_status}
                    if agent_spec.workflow_ref is not None
                    else None
                ),
            }
        )

    @app.get("/studio/agents/{agent_id}/channels")
    async def list_agent_channels(agent_id: str) -> JSONResponse:
        """TASK-014（§9.2）：Agent 渠道产品投影（Web Chat 正式 Channel，规则 15）。"""
        return success(
            await service.list_agent_channels(_actor(None), agent_id=agent_id)
        )

    @app.post("/studio/agents/{agent_id}/channels/web:verify")
    async def verify_agent_web_channel(
        agent_id: str,
        x_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    ) -> JSONResponse:
        # TASK-014：渠道 verify——真实链路检查（规则 18：显式失败清单，不静默）：
        # ① Agent 已发布；② 存在活跃 Web Chat 入口；③ Runtime resolve 链可构建
        # ExecutionSnapshot（仅 resolve，不执行模型；无 runtime 装配 → 503 fail-closed）。
        actor = _actor(x_actor_id)
        channels = await service.list_agent_channels(actor, agent_id=agent_id)
        web = channels["web"]
        problems: list[str] = []
        if web["status"] != "active":
            problems.append("Agent 未发布：Web Chat 入口要求已发布 Agent")
        entries = web["entries"]
        if not entries:
            problems.append("无活跃 Web Chat 入口：请先开通渠道并生成入口")
        if runtime_service is None:
            raise ConsoleError(
                RUNTIME_APPLICATION_ERROR,
                "channel verify requires runtime service",
                503,
            )
        from fluxion.agents.definitions import AgentDefinition as _AgentDefinition

        definition = await service.get_resource(
            actor, ResourceKind.AGENT_DEFINITION, agent_id
        )
        agent_spec = _AgentDefinition.model_validate(definition.spec_json)
        ref = agent_spec.runtime_profile_ref
        from fluxion.services.runtime_profile_resolution import (
            resolve_default_runtime_profile as _resolve_default,
        )

        if ref is not None:
            profile_id = ref.id
            profile_selector = ref.version
        else:
            default_profile = await _resolve_default(
                service.store, actor.tenant_id
            )
            if default_profile is None:
                raise ConsoleError(
                    RUNTIME_APPLICATION_ERROR,
                    "no default RuntimeProfile for channel verify (ADR-A010)",
                    409,
                )
            profile_id = default_profile.id
            profile_selector = "latest-published"
        from fluxion.services.runtime_contracts import RunRuntimeRequest
        from uuid import uuid4 as _uuid4

        resolve_summary: dict[str, object] | None = None
        if not problems:
            verify_user = str(entries[0]["platform_user_id"])
            request = RunRuntimeRequest(
                tenant_id=actor.tenant_id,
                user_id=verify_user,
                runtime_profile_id=profile_id,
                session_id=f"channel-verify-{_uuid4().hex[:12]}",
                input_message="",
                runtime_profile_version_selector=profile_selector,
                agent_definition_id=agent_id,
            )
            resolve_summary = await runtime_service.resolve_context(request)  # type: ignore[arg-type]
        return success(
            {
                "channel_type": "web",
                "ok": not problems,
                "problems": problems,
                "resolve": resolve_summary,
            }
        )
