import uuid
from typing import Annotated

from fastapi import Depends, Header, Request
from muad_api import AppError
from muad_api.context import current_tenant_id
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings

from ..application.ports import NullPlatformSettingsClient, PlatformSettingsClient

TENANT_HEADER = "X-Tenant-Id"
ACTOR_HEADER = "X-Actor-User-Id"

# 内部服务身份门控一律用 api-kit 原语（RULE-api-001「服务底座统一复用 api-kit 原语」）：
# 本模块此前自带一份同形实现，而 `/internal/admin/*` 用了它、`/internal/tasks` 与
# `/internal/schedules` 没用 —— 两处并行正是 2026-10-06 评审 #1 的成因（普通 Task/Schedule
# 接口可被任意内网调用方伪造租户头读写）。现在只有一份实现（`muad_api.security`），各路由
# 直接 `from muad_api.security import require_internal_service`。


def get_platform_settings_client(request: Request) -> PlatformSettingsClient:
    """取设置快照源（任务创建边界用）。

    lifespan 注入真实 `ConsolePlatformSettingsClient`；未进 lifespan 的直构/测试用空对象
    （等价于「该租户无记录」——revision 0 + schema 默认）。
    """
    client: PlatformSettingsClient | None = getattr(
        request.app.state, "platform_settings_client", None
    )
    return client if client is not None else NullPlatformSettingsClient()


PlatformSettingsClientDep = Annotated[
    PlatformSettingsClient, Depends(get_platform_settings_client)
]


def get_tenant_id() -> str:
    return current_tenant_id() or SharedSettings().default_tenant_id


def ensure_tenant_consistent(request: Request, expected_tenant_id: str) -> None:
    header_tenant_id = request.headers.get(TENANT_HEADER, "")
    if header_tenant_id and header_tenant_id != expected_tenant_id:
        raise AppError(ErrorCode.COMMON_BAD_REQUEST)


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
