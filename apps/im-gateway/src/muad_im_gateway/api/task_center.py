"""任务中心的 surface 路由（design §3.4.1、§3.4.6 API-12..19/25/31；TASK-024）。

- **一组 handler，按 surface 挂载**：`build_task_center_router(surface)` 的路径只由
  `TaskCenterSurface.name` 决定（本需求挂 WebChat；MSSWWEB 路由另立需求、在本需求里不存在）。
  同一 service 也服务模型工具之外的其它 surface —— surface 只影响前缀与确认来源。
- **主体门控沿用 WebChat**：读写全部经 `ChatIdentityDep`（CHAT Cookie + `X-Chat-Context`
  只经 Console 核验），本层不做第二套认证；Agent/租户范围由 service 与本次核验的授权集合取交集。
- **写请求带 `Idempotency-Key`**：动作/回答/确认重放返回首次结果；确认路由同时把该键当作
  `confirmation_ref`（一次用户动作 = 一个稳定引用），模型工具路径没有这条路由。
- **下载只按引用取件**：客户端给 `artifact_id + version + checksum`，字节由 Owner 解析的存储键
  读取；响应带 `no-store`/`nosniff` 与服务端裁决的 `Content-Disposition`。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import FileResponse
from muad_api import ApiResponse, AppError, ok
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    HumanRespondRequest,
    ScheduleActionRequest,
    ScheduleDraftConfirmRequest,
    ScheduleMode,
    ScheduleStatus,
    TaskStatus,
)

from ..application.task_center import TaskCenterService, TaskCenterSurface
from .deps import ChatIdentityDep
from .webchat import download_response, no_store

#: 写请求的稳定动作身份：还用作 surface 确认引用（Draft 的 `confirmation_ref`）。
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1)]
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int | None, Query(ge=1, le=MAX_PAGE_SIZE)]
AgentFilter = Annotated[uuid.UUID | None, Query()]


def get_task_center_service(request: Request) -> TaskCenterService:
    """任务中心服务：Worker 读取面 + Runtime 产物版本解析 + 共享存储（装配根发布的状态值）。"""
    owner = getattr(request.app.state, "worker_client", None)
    artifacts = getattr(request.app.state, "runtime_client", None)
    store = getattr(request.app.state, "artifact_store", None)
    tenant_id = getattr(request.app.state, "tenant_id", None)
    if owner is None or artifacts is None or store is None or not tenant_id:
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return TaskCenterService(owner=owner, artifacts=artifacts, store=store, tenant_id=tenant_id)


TaskCenterServiceDep = Annotated[TaskCenterService, Depends(get_task_center_service)]


def build_task_center_router(surface: TaskCenterSurface) -> APIRouter:
    """按 ChatSurface 挂载任务中心；各 surface 复用同一组 handler 与同一份契约。"""
    router = APIRouter(
        prefix=f"/api/v1/{surface.name}",
        tags=["task-center"],
        dependencies=[Depends(no_store)],
    )

    @router.get("/task-center/tasks")
    async def list_tasks(
        request: Request,
        chat: ChatIdentityDep,
        service: TaskCenterServiceDep,
        agent_id: AgentFilter = None,
        status: Annotated[TaskStatus | None, Query()] = None,
        page: PageNumber = 1,
        page_size: PageSize = None,
    ) -> ApiResponse[Any]:
        """后台任务列表：与定时任务共用授权 Agent 范围，只给任务记录摘要。"""
        data = await service.list_tasks(
            chat,
            agent_id=agent_id,
            status=status,
            page=page,
            page_size=page_size or DEFAULT_PAGE_SIZE,
        )
        return ok(request.app.state.message_catalog, data)

    @router.get("/task-center/schedules")
    async def list_schedules(
        request: Request,
        chat: ChatIdentityDep,
        service: TaskCenterServiceDep,
        agent_id: AgentFilter = None,
        status: Annotated[ScheduleStatus | None, Query()] = None,
        mode: Annotated[ScheduleMode | None, Query()] = None,
        page: PageNumber = 1,
        page_size: PageSize = None,
    ) -> ApiResponse[Any]:
        """API-12：授权 Agent 范围内的任务列表（`agent_id` 只与当前授权集合取交集）。"""
        data = await service.list_schedules(
            chat,
            agent_id=agent_id,
            status=status,
            mode=mode,
            page=page,
            page_size=page_size or DEFAULT_PAGE_SIZE,
        )
        return ok(request.app.state.message_catalog, data)

    @router.get("/task-center/schedules/{schedule_id}")
    async def get_schedule(
        schedule_id: uuid.UUID,
        request: Request,
        chat: ChatIdentityDep,
        service: TaskCenterServiceDep,
        agent_id: AgentFilter = None,
    ) -> ApiResponse[Any]:
        """API-13：详情与列表同一范围；撤销授权/不存在的 Schedule 都是 404。"""
        detail = await service.get_schedule(chat, schedule_id, agent_id=agent_id)
        return ok(
            request.app.state.message_catalog,
            detail.model_dump(mode="json", exclude={"actor_user_id"}),
        )

    @router.get("/task-center/schedules/{schedule_id}/fires")
    async def list_fires(
        schedule_id: uuid.UUID,
        request: Request,
        chat: ChatIdentityDep,
        service: TaskCenterServiceDep,
        page: PageNumber = 1,
        page_size: PageSize = None,
    ) -> ApiResponse[Any]:
        """API-14：真实 Fire 台账 + DST 事件（不伪造 Fire）。"""
        data = await service.list_fires(
            chat, schedule_id, page=page, page_size=page_size or DEFAULT_PAGE_SIZE
        )
        return ok(request.app.state.message_catalog, data)

    @router.get("/task-center/executions/{execution_id}")
    async def get_execution(
        execution_id: uuid.UUID,
        request: Request,
        chat: ChatIdentityDep,
        service: TaskCenterServiceDep,
        children_page: Annotated[int | None, Query(ge=1)] = None,
        children_page_size: Annotated[int | None, Query(ge=1, le=MAX_PAGE_SIZE)] = None,
    ) -> ApiResponse[Any]:
        """API-15：Execution 详情（结果/错误/等待类型/产物引用 + 可选子任务页）。"""
        data = await service.get_execution(
            chat,
            execution_id,
            children_page=children_page,
            children_page_size=children_page_size,
        )
        return ok(request.app.state.message_catalog, data)

    @router.get("/task-center/pending")
    async def list_pending(
        request: Request,
        chat: ChatIdentityDep,
        service: TaskCenterServiceDep,
        agent_id: AgentFilter = None,
        kind: Annotated[str | None, Query(pattern="^(HUMAN|NEEDS_ATTENTION)$")] = None,
        page: PageNumber = 1,
        page_size: PageSize = None,
    ) -> ApiResponse[Any]:
        """API-16：待处理（HUMAN 与 NEEDS_ATTENTION 混排，统一分页）。"""
        data = await service.list_pending(
            chat,
            agent_id=agent_id,
            kind=kind,
            page=page,
            page_size=page_size or DEFAULT_PAGE_SIZE,
        )
        return ok(request.app.state.message_catalog, data)

    @router.get("/task-center/badges")
    async def get_badges(
        request: Request,
        chat: ChatIdentityDep,
        service: TaskCenterServiceDep,
        agent_id: AgentFilter = None,
    ) -> ApiResponse[Any]:
        """API-17：徽标（与列表/待处理同一 Agent 范围）。"""
        data = await service.badges(chat, agent_id=agent_id)
        return ok(request.app.state.message_catalog, data)

    @router.post("/task-center/human-interactions/{interaction_id}/respond")
    async def respond_human(
        interaction_id: uuid.UUID,
        payload: HumanRespondRequest,
        request: Request,
        chat: ChatIdentityDep,
        service: TaskCenterServiceDep,
        idempotency_key: IdempotencyKey,
        agent_id: AgentFilter = None,
    ) -> ApiResponse[Any]:
        """API-18：回答 Human 提问（幂等键原样交给 Owner 的台账）。"""
        data = await service.respond_human(
            chat,
            interaction_id,
            payload,
            agent_id=agent_id,
            idempotency_key=idempotency_key,
        )
        return ok(request.app.state.message_catalog, data)

    @router.post("/task-center/schedules/{schedule_id}/actions")
    async def schedule_action(
        schedule_id: uuid.UUID,
        payload: ScheduleActionRequest,
        request: Request,
        chat: ChatIdentityDep,
        service: TaskCenterServiceDep,
        idempotency_key: IdempotencyKey,
    ) -> ApiResponse[Any]:
        """API-19：pause/resume/cancel（与内部 API 共用 Owner 的 ScheduleService）。"""
        data = await service.schedule_action(
            chat, schedule_id, payload, idempotency_key=idempotency_key
        )
        return ok(request.app.state.message_catalog, data)

    @router.get("/task-center/artifacts/{artifact_id}")
    async def download_artifact(
        artifact_id: uuid.UUID,
        chat: ChatIdentityDep,
        service: TaskCenterServiceDep,
        version: Annotated[str, Query(min_length=1, max_length=64)],
        checksum: Annotated[str, Query(min_length=1, max_length=128)],
    ) -> FileResponse:
        """API-25：按任务 owner+agent 下载**指定版本**（版本/校验值缺一不可）。"""
        download = await service.open_artifact(
            chat, artifact_id, version=version, checksum=checksum
        )
        return download_response(download)

    @router.post("/schedule-drafts/{draft_id}/confirm")
    async def confirm_draft(
        draft_id: uuid.UUID,
        payload: ScheduleDraftConfirmRequest,
        request: Request,
        chat: ChatIdentityDep,
        service: TaskCenterServiceDep,
        idempotency_key: IdempotencyKey,
    ) -> ApiResponse[Any]:
        """API-31 → API-27：surface 侧的用户确认（带着可信 actor 与确认来源）。"""
        data = await service.confirm_draft(
            chat,
            draft_id,
            payload,
            surface=surface.confirmation_surface,
            idempotency_key=idempotency_key,
        )
        return ok(request.app.state.message_catalog, data)

    return router
