"""API-10：Human 回答（design §3.4.2 / §3.4.6 / §3.5.8）。

回答是**按 interaction_id 的显式动作**：问题不属于任何 Conversation，服务端不按文本猜。
两侧 CAS（interaction OPEN + execution WAITING_HUMAN）与幂等都在
`application/human_interactions.py`；本路由只做身份/幂等台账/封套。

台账 `human-respond` 与 interaction 级幂等互补：`Idempotency-Key` 管"同一次提交重放"，
回答内容指纹管"同一问题被不同请求以不同答案二次回答"（后者必须冲突，不能静默覆盖）。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from muad_api import ApiResponse, ok
from muad_api.security import require_internal_service
from muad_contracts import HumanInteractionStatus, HumanRespondRequest, HumanRespondResponse, TaskStatus
from sqlalchemy.ext.asyncio import AsyncSession

from ..application.human_interactions import HumanInteractionService
from ..application.submissions import (
    IDEMPOTENCY_HEADER,
    SubmissionEndpoint,
    TaskSubmissionService,
    resolve_idempotency_key,
    submission_fingerprint,
)
from ..infrastructure.db import get_session
from ..infrastructure.db_clock import database_now
from .deps import AgentId, RequiredActorUserId, ensure_tenant_consistent, get_tenant_id

router = APIRouter(
    prefix="/internal/human-interactions",
    tags=["human-interactions"],
    dependencies=[Depends(require_internal_service)],
)

TenantId = Annotated[str, Depends(get_tenant_id)]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("/{interaction_id}/respond")
async def respond(
    interaction_id: uuid.UUID,
    body: HumanRespondRequest,
    request: Request,
    tenant_id: TenantId,
    session: Session,
    actor_user_id: RequiredActorUserId,
    agent_id: AgentId,
) -> ApiResponse[Any]:
    ensure_tenant_consistent(request, tenant_id)
    idempotency_key = resolve_idempotency_key(request.headers.get(IDEMPOTENCY_HEADER), None)
    fingerprint = submission_fingerprint(
        endpoint=SubmissionEndpoint.HUMAN_RESPOND,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        target_id=interaction_id,
        action=body,
    )
    submissions = TaskSubmissionService(session)
    first = await submissions.admit(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        endpoint=SubmissionEndpoint.HUMAN_RESPOND,
        fingerprint=fingerprint,
    )
    if first is not None:
        return ok(request.app.state.message_catalog, first.response_json)
    closed_id, execution_id, execution_status = await HumanInteractionService(
        session, now=await database_now(session)
    ).respond(
        interaction_id,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        agent_id=agent_id,
        response=body.response,
    )
    payload = HumanRespondResponse(
        interaction_id=closed_id,
        status=HumanInteractionStatus.RESOLVED,
        execution_id=execution_id,
        execution_status=TaskStatus(execution_status),
    ).model_dump(mode="json")
    await submissions.record_in(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        endpoint=SubmissionEndpoint.HUMAN_RESPOND,
        actor_user_id=actor_user_id,
        request_fingerprint=fingerprint,
        response=payload,
        task_id=execution_id,
    )
    await session.commit()
    return ok(request.app.state.message_catalog, payload)


__all__ = ["router"]
