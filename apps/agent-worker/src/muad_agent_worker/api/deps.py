import uuid
from typing import Annotated

from fastapi import Depends, Header, Request
from muad_api import AppError
from muad_api.context import current_tenant_id
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings

TENANT_HEADER = "X-Tenant-Id"
INTERNAL_SERVICE_HEADER = "X-Internal-Service"
ACTOR_HEADER = "X-Actor-User-Id"


def get_tenant_id() -> str:
    return current_tenant_id() or SharedSettings().default_tenant_id


def ensure_tenant_consistent(request: Request, expected_tenant_id: str) -> None:
    header_tenant_id = request.headers.get(TENANT_HEADER, "")
    if header_tenant_id and header_tenant_id != expected_tenant_id:
        raise AppError(ErrorCode.COMMON_BAD_REQUEST)


def require_internal_service(
    x_internal_service: Annotated[str | None, Header()] = None,
) -> None:
    """Admin API 只允许受信内部调用方；缺失或错误身份一律 FORBIDDEN。"""
    expected = SharedSettings().internal_service_token
    if not expected or x_internal_service != expected:
        raise AppError(ErrorCode.FORBIDDEN)


InternalServiceDep = Annotated[None, Depends(require_internal_service)]


def get_actor_user_id(
    x_actor_user_id: Annotated[str | None, Header()] = None,
) -> uuid.UUID | None:
    """Runtime 代表已认证 Run 用户调用时声明的 actor；缺省为 None（不限定归属）。"""
    if x_actor_user_id is None:
        return None
    try:
        return uuid.UUID(x_actor_user_id)
    except ValueError as exc:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from exc


def require_actor_user_id(
    actor_user_id: Annotated[uuid.UUID | None, Depends(get_actor_user_id)],
) -> uuid.UUID:
    """Runtime 侧的 Schedule 变更必须声明 actor，由 service 校验 owner（API-07/08）。"""
    if actor_user_id is None:
        raise AppError(ErrorCode.FORBIDDEN)
    return actor_user_id


ActorUserId = Annotated[uuid.UUID | None, Depends(get_actor_user_id)]
RequiredActorUserId = Annotated[uuid.UUID, Depends(require_actor_user_id)]
