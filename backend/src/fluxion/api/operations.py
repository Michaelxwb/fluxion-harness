"""Operations 运营路由（Phase 5 TASK-010 / FEAT-P5-07，S-11）。

`GET /api/v1/operations/queues|workers`——DBOS sysdb 只读 + 统一 envelope
（{code,message,data,request_id}）。数据为部署级（sysdb 无租户列）；
tenant 经请求上下文记录（rule 16 API 层面）。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from fluxion.api.responses import failure, success
from fluxion.errors.console import OPERATIONS_UNAVAILABLE
from fluxion.services.operations_app import OperationsApplicationService


def register_operations_routes(
    app: FastAPI,
    service: OperationsApplicationService | None = None,
) -> None:
    """注册 operations 只读路由（恒注册；未装配 sysdb → 空数据，契约稳定）。"""

    @app.get("/api/v1/operations/queues")
    async def list_queues() -> JSONResponse:
        try:
            queues = await service.list_queues() if service is not None else []
        except Exception as error:  # noqa: BLE001 —— sysdb 不可达须回统一 envelope
            return failure(
                OPERATIONS_UNAVAILABLE,
                f"operations.queues 不可用: {error}",
                status_code=503,
            )
        return success(queues)

    @app.get("/api/v1/operations/workers")
    async def list_workers() -> JSONResponse:
        try:
            workers = await service.list_workers() if service is not None else []
        except Exception as error:  # noqa: BLE001 —— sysdb 不可达须回统一 envelope
            return failure(
                OPERATIONS_UNAVAILABLE,
                f"operations.workers 不可用: {error}",
                status_code=503,
            )
        return success(workers)
