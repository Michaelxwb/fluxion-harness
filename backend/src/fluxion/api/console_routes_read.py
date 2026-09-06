from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from fluxion.api.console_helpers import _actor, _page, _status
from fluxion.api.responses import success
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.console_payloads import (
    audit_payload,
    credential_payload,
    policy_payload,
    run_payload,
    trace_payload,
)


def _register_p1_routes(app: FastAPI, service: ConsoleApplicationService) -> None:
    @app.get("/api/v1/policies")
    async def list_policies(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        keyword: Annotated[str | None, Query()] = None,
        status: Annotated[str | None, Query()] = None,
    ) -> JSONResponse:
        items, total = await service.list_policies(
            _actor(None), page=page, page_size=page_size, keyword=keyword,
            status=_status(status),
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
    # FEAT-04：静态投影路由必须先于任何动态 credential 路由注册，避免被捕获。
    @app.get("/api/v1/credentials/projection")
    async def get_credential_projection(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        keyword: Annotated[str | None, Query()] = None,
        purpose: Annotated[str | None, Query()] = None,
        status: Annotated[str | None, Query()] = None,
        revoked: Annotated[bool | None, Query()] = None,
    ) -> JSONResponse:
        """Credential Projection 只读查询：固定 3 SQL 替代客户端 N+1。

        tenant 取可信认证上下文，不接受查询参数切换；响应经统一 success
        envelope，Handler 不手写结构；不读密文、不解密。
        """
        items, total = await service.list_credential_projection(
            _actor(None),
            page=page,
            page_size=page_size,
            keyword=keyword,
            purpose=purpose,
            status=_status(status),
            revoked=revoked,
        )
        return success(
            _page([item.to_payload() for item in items], page, page_size, total)
        )

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
        status: Annotated[str | None, Query()] = None,
        keyword: Annotated[str | None, Query()] = None,
    ) -> JSONResponse:
        items, total = await service.list_runs(
            _actor(None), page=page, page_size=page_size, status=status, keyword=keyword
        )
        return success(_page([run_payload(item) for item in items], page, page_size, total))

    @app.get("/api/v1/runs/{execution_id}")
    async def get_run(execution_id: str) -> JSONResponse:
        return success(run_payload(await service.get_run(_actor(None), execution_id)))

    @app.get("/api/v1/audit")
    async def list_audit(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        action: Annotated[str | None, Query()] = None,
        actor_id: Annotated[str | None, Query()] = None,
        target_type: Annotated[str | None, Query()] = None,
        created_from: Annotated[str | None, Query()] = None,
        created_to: Annotated[str | None, Query()] = None,
    ) -> JSONResponse:
        # TASK-021（§8.10）：审计组合过滤（后端查询参数下推）
        items, total = await service.list_audit(
            _actor(None),
            page=page,
            page_size=page_size,
            action=action,
            actor_id=actor_id,
            target_type=target_type,
            created_from=created_from,
            created_to=created_to,
        )
        return success(_page([audit_payload(item) for item in items], page, page_size, total))
