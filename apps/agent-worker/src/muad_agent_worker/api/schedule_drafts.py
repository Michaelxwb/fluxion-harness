"""API-01 / API-27：Schedule Draft 的创建与用户确认（design §3.4.3 / §3.4.4 / §3.5.4）。

请求面只有「待确认配置」一条事实：身份（租户/owner/agent/入口 surface）来自受信内部服务
上下文与请求头，body 不接受 owner；创建本身经 `task_submission` 幂等台账（RULE-api-002）：
同 `Idempotency-Key` + 同指纹重放首次 Draft（即使它已 CANCELLED/EXPIRED），异指纹冲突。

副作用边界：创建端点只写一条 PENDING Draft 与一条首次响应快照；不建 ACTIVE Schedule，
不建 Fire/Execution/Delivery。确认端点（API-27）是唯一的创建/更新生效入口，且要求
surface 侧的用户动作来源（`X-Confirmation-Surface` + `X-Confirmation-Ref`）——模型工具
上下文没有这条通路，同轮自确认在这里被拒绝。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from muad_api import ApiResponse, AppError, ok
from muad_api.error_codes import ErrorCode
from muad_api.security import require_internal_service
from muad_contracts import ScheduleDraftConfirmRequest
from muad_contracts.platform_settings import PlatformSettings
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.platform_settings import resolve_platform_settings
from ..application.schedule_confirm import ConfirmIdentity, ScheduleConfirmService
from ..application.schedule_drafts import (
    DraftIdentity,
    ScheduleDraftService,
    load_draft,
    parse_draft_request,
)
from ..application.submissions import (
    IDEMPOTENCY_HEADER,
    SubmissionEndpoint,
    TaskSubmissionService,
    resolve_idempotency_key,
    submission_fingerprint,
)
from ..infrastructure.db import get_session
from ..infrastructure.db_clock import database_now
from .deps import (
    ConfirmationScope,
    EntryChannel,
    PlatformSettingsClientDep,
    RequiredActorUserId,
    RequiredAgentId,
    ScheduleMaterialsClientDep,
    get_tenant_id,
)

router = APIRouter(
    prefix="/internal/schedule-drafts",
    tags=["schedules"],
    dependencies=[Depends(require_internal_service)],
)

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]


async def _settings(
    settings_client: PlatformSettingsClientDep, tenant_id: str
) -> PlatformSettings:
    snapshot = await settings_client.fetch_snapshot(tenant_id=tenant_id)
    return resolve_platform_settings(snapshot)


@router.post("")
async def create_schedule_draft(
    body: dict[str, Any],
    request: Request,
    tenant_id: TenantId,
    session: Session,
    actor_user_id: RequiredActorUserId,
    agent_id: RequiredAgentId,
    entry_channel: EntryChannel,
    materials_client: ScheduleMaterialsClientDep,
    settings_client: PlatformSettingsClientDep,
) -> ApiResponse[Any]:
    parsed = parse_draft_request(body)
    idempotency_key = resolve_idempotency_key(request.headers.get(IDEMPOTENCY_HEADER), None)
    fingerprint = submission_fingerprint(
        endpoint=SubmissionEndpoint.SCHEDULE_DRAFTS,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        target_id=parsed.base_schedule_id,
        # 入口 surface 决定投递能力，因而是同一逻辑命令的关键参数。
        action={**body, "entry_channel": entry_channel},
    )
    submissions = TaskSubmissionService(session)
    first = await submissions.admit(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        endpoint=SubmissionEndpoint.SCHEDULE_DRAFTS,
        fingerprint=fingerprint,
    )
    if first is not None:
        # 重放优先于任何状态检查：首次 Draft 的响应不因后来取消/过期而改变。
        return ok(request.app.state.message_catalog, first.response_json)
    service = ScheduleDraftService(
        session,
        materials_client=materials_client,
        settings=await _settings(settings_client, tenant_id),
        now=await database_now(session),
    )
    response = await service.create(
        parsed,
        identity=DraftIdentity(
            tenant_id=tenant_id,
            owner_user_id=actor_user_id,
            agent_id=agent_id,
            entry_channel=entry_channel,
        ),
        idempotency_key=idempotency_key,
    )
    await submissions.record_in(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        endpoint=SubmissionEndpoint.SCHEDULE_DRAFTS,
        actor_user_id=actor_user_id,
        request_fingerprint=fingerprint,
        response=response,
        schedule_draft_id=uuid.UUID(response["draft_id"]),
    )
    # 先提交再响应：确认端点在另一个请求里按 id 读取 Draft。
    await session.commit()
    return ok(request.app.state.message_catalog, response)


@router.get("/{draft_id}")
async def read_schedule_draft(
    draft_id: uuid.UUID,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    actor_user_id: RequiredActorUserId,
    agent_id: RequiredAgentId,
) -> ApiResponse[Any]:
    """API-01 读面：聊天入口按 id 取回自己的 Draft（hash/预览/过期），不接受自报 hash。"""
    data = await load_draft(
        session,
        draft_id,
        identity=DraftIdentity(
            tenant_id=tenant_id, owner_user_id=actor_user_id, agent_id=agent_id
        ),
    )
    return ok(request.app.state.message_catalog, data)


def _parse_confirm(body: dict[str, Any]) -> ScheduleDraftConfirmRequest:
    """确认请求体的显式失败：字段形状非法（含未知字段）不静默忽略。"""
    try:
        return ScheduleDraftConfirmRequest.model_validate(body)
    except ValidationError as exc:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from exc


@router.post("/{draft_id}/confirm")
async def confirm_schedule_draft(
    draft_id: uuid.UUID,
    body: dict[str, Any],
    request: Request,
    tenant_id: TenantId,
    session: Session,
    actor_user_id: RequiredActorUserId,
    scope: ConfirmationScope,
) -> ApiResponse[Any]:
    """API-27：用户确认 Draft 并执行（创建/更新）。确认请求本体就是唯一生效入口。"""
    parsed = _parse_confirm(body)
    idempotency_key = resolve_idempotency_key(request.headers.get(IDEMPOTENCY_HEADER), None)
    fingerprint = submission_fingerprint(
        endpoint=SubmissionEndpoint.SCHEDULE_CONFIRM,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        target_id=draft_id,
        # 用户动作来源是同一逻辑命令的关键参数：换一个动作引用就是另一条命令。
        action={
            **body,
            "confirmation_surface": str(scope.surface) if scope is not None else None,
            "confirmation_ref": scope.ref if scope is not None else None,
        },
    )
    submissions = TaskSubmissionService(session)
    first = await submissions.admit(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        endpoint=SubmissionEndpoint.SCHEDULE_CONFIRM,
        fingerprint=fingerprint,
    )
    if first is not None:
        # 重放优先于过期/状态检查：同键同指纹即使 Draft 已 CONFIRMED 也原样返回首次结果。
        return ok(request.app.state.message_catalog, first.response_json)
    service = ScheduleConfirmService(
        session,
        submissions=submissions,
        now=await database_now(session),
    )
    response = await service.confirm(
        draft_id,
        request=parsed,
        identity=ConfirmIdentity(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            surface=scope.surface if scope is not None else None,
            ref=scope.ref if scope is not None else None,
        ),
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    )
    await session.commit()
    return ok(request.app.state.message_catalog, response)
