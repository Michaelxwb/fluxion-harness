"""Trusted operation cancellation with immutable first-response replay."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from muad_api import ApiResponse, ok
from muad_api.security import require_internal_service
from muad_contracts import CancelOperationRequest, CancelOperationResponse
from sqlalchemy import text

from ..application.runtime_operations import cancel_operation
from ..application.submissions import TaskSubmissionService, resolve_idempotency_key, submission_fingerprint
from .tasks import Session, TenantId, _publish_cancel_hint

router = APIRouter(
    prefix="/internal/runtime-operations",
    tags=["runtime-operations"],
    dependencies=[Depends(require_internal_service)],
)


@router.post("/{operation_id}/cancel")
async def cancel_runtime_operation(
    operation_id: UUID, body: CancelOperationRequest, request: Request, session: Session, tenant: TenantId
) -> ApiResponse[CancelOperationResponse]:
    endpoint = "cancel-runtime-operation"
    key = resolve_idempotency_key(request.headers.get("Idempotency-Key"), f"runtime-op:{operation_id}:cancel")
    fingerprint = submission_fingerprint(
        endpoint, body.model_dump(mode="json") | {"tenant_id": tenant, "operation_id": str(operation_id)}
    )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
        {"identity": f"{tenant}:{endpoint}:{key}"},
    )
    submissions = TaskSubmissionService(session)
    replay = await submissions.find_replay(
        tenant_id=tenant, idempotency_key=key, endpoint=endpoint, fingerprint=fingerprint
    )
    if replay is not None:
        return ok(
            request.app.state.message_catalog, CancelOperationResponse.model_validate(replay.response_json)
        )
    response = await cancel_operation(session, tenant, operation_id, body)
    await submissions.record_in(
        tenant_id=tenant,
        idempotency_key=key,
        endpoint=endpoint,
        actor_user_id=body.actor_user_id,
        request_fingerprint=fingerprint,
        response=response.model_dump(mode="json"),
        task_id=response.task_id,
    )
    await session.commit()
    if response.task_id is not None and response.task_status is not None:
        await _publish_cancel_hint(
            request, response.task_id, response.task_status.value, response.cancel_requested
        )
    return ok(request.app.state.message_catalog, response)
