"""Chat Workspace 路由（Phase 5 TASK-014 / FEAT-P5-10，S-15）。

`/api/v1/workspace/*`——phase4 X402-X408 冻结契约的后端实现。身份来自 Bearer
Chat Access Token（与 Channel API 同一鉴权面；不要求 X-Tenant-ID header），
统一 envelope（`{code, message, data, request_id}`，rule 22）。
"""

from __future__ import annotations

import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from fluxion.api.middleware import RequestContextMiddleware
from fluxion.api.responses import failure, success
from fluxion.config import DevModeSettings
from fluxion.errors.console import INTERNAL_ERROR, VALIDATION_FAILED, ConsoleError
from fluxion.observability.logging import emit_error_log
from fluxion.services.workspace_app import WorkspaceApplicationService


class ApprovalDecisionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=2048)


class ProfilePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform_user_id: str
    display_name: str = Field(min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    timezone: str | None = Field(default=None, max_length=64)
    locale: str | None = Field(default=None, max_length=16)


class MemoryCorrectPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4096)


class AutoLearnPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


def create_app(
    service: WorkspaceApplicationService,
    *,
    dev_mode: DevModeSettings | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield

    app = FastAPI(title="Fluxion Chat Workspace API", lifespan=_lifespan)
    # 鉴权在 Bearer token 层（token→tenant/user），不要求 Console 身份头。
    app.add_middleware(
        RequestContextMiddleware, dev_mode=dev_mode, require_identity=False
    )
    _register_errors(app)
    _register_routes(app, service)
    return app


def _register_errors(app: FastAPI) -> None:
    @app.exception_handler(ConsoleError)
    async def console_error(request: Request, exc: ConsoleError) -> JSONResponse:
        return failure(exc.code, exc.message, status_code=exc.status_code, request=request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        del exc
        return failure(
            VALIDATION_FAILED, "请求参数无效", status_code=400, request=request
        )

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        # 与 Channel/Console API 对齐：未捕获异常回统一 envelope，不裸 500。
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
        return failure(INTERNAL_ERROR, "internal error", status_code=500, request=request)


def _register_routes(app: FastAPI, service: WorkspaceApplicationService) -> None:
    @app.get("/api/v1/workspace/agents")
    async def list_agents(
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> JSONResponse:
        identity = await service.resolve_identity(_bearer_token(authorization))
        return success(await service.list_agents(tenant_id=identity.tenant_id))

    @app.get("/api/v1/workspace/tasks")
    async def list_tasks(
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> JSONResponse:
        identity = await service.resolve_identity(_bearer_token(authorization))
        return success(
            await service.list_tasks(tenant_id=identity.tenant_id, user_id=identity.platform_user_id)
        )

    @app.get("/api/v1/workspace/tasks/{task_id}")
    async def get_task(
        task_id: str,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> JSONResponse:
        identity = await service.resolve_identity(_bearer_token(authorization))
        return success(
            await service.get_task(
                tenant_id=identity.tenant_id,
                user_id=identity.platform_user_id,
                task_id=task_id,
            )
        )

    @app.get("/api/v1/workspace/approvals")
    async def list_approvals(
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> JSONResponse:
        identity = await service.resolve_identity(_bearer_token(authorization))
        return success(await service.list_approvals(tenant_id=identity.tenant_id))

    @app.post("/api/v1/workspace/approvals/{approval_id}/decision")
    async def decide_approval(
        approval_id: str,
        payload: ApprovalDecisionPayload,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> JSONResponse:
        identity = await service.resolve_identity(_bearer_token(authorization))
        await service.decide_approval(
            tenant_id=identity.tenant_id,
            user_id=identity.platform_user_id,
            approval_id=approval_id,
            decision=payload.decision,
            comment=payload.comment,
        )
        return success(None)

    @app.get("/api/v1/workspace/history")
    async def list_history(
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> JSONResponse:
        identity = await service.resolve_identity(_bearer_token(authorization))
        return success(
            await service.list_history(
                tenant_id=identity.tenant_id, user_id=identity.platform_user_id
            )
        )

    @app.get("/api/v1/workspace/profile")
    async def get_profile(
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> JSONResponse:
        identity = await service.resolve_identity(_bearer_token(authorization))
        return success(
            await service.get_profile(
                tenant_id=identity.tenant_id, user_id=identity.platform_user_id
            )
        )

    @app.put("/api/v1/workspace/profile")
    async def update_profile(
        payload: ProfilePayload,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> JSONResponse:
        identity = await service.resolve_identity(_bearer_token(authorization))
        return success(
            await service.update_profile(
                tenant_id=identity.tenant_id,
                user_id=identity.platform_user_id,
                payload=payload.model_dump(),
            )
        )

    @app.get("/api/v1/workspace/memory")
    async def list_memory(
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> JSONResponse:
        identity = await service.resolve_identity(_bearer_token(authorization))
        return success(
            await service.list_memory(
                tenant_id=identity.tenant_id, user_id=identity.platform_user_id
            )
        )

    # auto-learn 固定段路由先注册（与 /memory/{memory_id} 无方法冲突，注册序防御）。
    @app.get("/api/v1/workspace/memory/auto-learn")
    async def get_auto_learn(
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> JSONResponse:
        identity = await service.resolve_identity(_bearer_token(authorization))
        return success(
            await service.get_auto_learn(
                tenant_id=identity.tenant_id, user_id=identity.platform_user_id
            )
        )

    @app.put("/api/v1/workspace/memory/auto-learn")
    async def set_auto_learn(
        payload: AutoLearnPayload,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> JSONResponse:
        identity = await service.resolve_identity(_bearer_token(authorization))
        return success(
            await service.set_auto_learn(
                tenant_id=identity.tenant_id,
                user_id=identity.platform_user_id,
                enabled=payload.enabled,
            )
        )

    @app.patch("/api/v1/workspace/memory/{memory_id}")
    async def correct_memory(
        memory_id: str,
        payload: MemoryCorrectPayload,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> JSONResponse:
        identity = await service.resolve_identity(_bearer_token(authorization))
        return success(
            await service.correct_memory(
                tenant_id=identity.tenant_id,
                user_id=identity.platform_user_id,
                memory_id=memory_id,
                content=payload.content,
            )
        )

    @app.delete("/api/v1/workspace/memory/{memory_id}")
    async def delete_memory(
        memory_id: str,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> JSONResponse:
        identity = await service.resolve_identity(_bearer_token(authorization))
        await service.delete_memory(
            tenant_id=identity.tenant_id,
            user_id=identity.platform_user_id,
            memory_id=memory_id,
        )
        return success(None)


def _bearer_token(authorization: str | None) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _state_or_header(request: Request, state_key: str, header_name: str) -> str:
    state_id = getattr(request.state, state_key, None)
    if isinstance(state_id, str) and state_id:
        return state_id
    return request.headers.get(header_name, "")


def _state_or_unknown(request: Request, state_key: str) -> str:
    state_id = getattr(request.state, state_key, None)
    if isinstance(state_id, str) and state_id:
        return state_id
    return "unknown"
