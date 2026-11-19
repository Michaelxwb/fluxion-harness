import uuid
from typing import Annotated

from fastapi import Depends, Header, Request
from muad_api import AppError
from muad_api.context import current_tenant_id
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings
from muad_contracts import ConfirmationSurface, ConfirmScope

from ..application.ports import (
    ChannelTargetsClient,
    NullChannelTargetsClient,
    NullPlatformSettingsClient,
    PlatformSettingsClient,
)
from ..application.remote_cancellation import RuntimeToolOperationCanceller, ToolOperationCanceller
from ..scheduler.client import ConsoleResolveClient, ScheduleMaterialsProtocol

TENANT_HEADER = "X-Tenant-Id"
ACTOR_HEADER = "X-Actor-User-Id"
#: Draft 的 Agent 归属与入口 surface 同样是可信上下文的一部分（不在 body 里）。
AGENT_HEADER = "X-Agent-Id"
ENTRY_CHANNEL_HEADER = "X-Entry-Channel"
#: 用户确认动作的来源（surface 侧持久标识）；只在 surface 真实核验用户动作后出现。
CONFIRMATION_SURFACE_HEADER = "X-Confirmation-Surface"
CONFIRMATION_REF_HEADER = "X-Confirmation-Ref"

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


def get_channel_targets_client(request: Request) -> ChannelTargetsClient:
    """用户已绑定的出站目标源；未进 lifespan 的直构/测试默认没有目标。"""
    client: ChannelTargetsClient | None = getattr(
        request.app.state, "channel_targets_client", None
    )
    return client if client is not None else NullChannelTargetsClient()


ChannelTargetsClientDep = Annotated[
    ChannelTargetsClient, Depends(get_channel_targets_client)
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


def get_agent_id(x_agent_id: Annotated[str | None, Header()] = None) -> uuid.UUID | None:
    """调用方声明的 Agent 归属（Draft/确认的归属复核用）。"""
    if x_agent_id is None:
        return None
    try:
        return uuid.UUID(x_agent_id)
    except ValueError as exc:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from exc


def require_agent_id(agent_id: Annotated[uuid.UUID | None, Depends(get_agent_id)]) -> uuid.UUID:
    """Draft 的 Agent 归属必须来自可信上下文；缺省即拒绝。"""
    if agent_id is None:
        raise AppError(ErrorCode.FORBIDDEN)
    return agent_id


def get_entry_channel(
    x_entry_channel: Annotated[str | None, Header()] = None,
) -> str | None:
    """入口 surface（投递能力的唯一来源）；规范化大写，空串等同缺省。"""
    if x_entry_channel is None:
        return None
    channel = x_entry_channel.strip().upper()
    return channel or None


def get_schedule_materials_client(request: Request) -> ScheduleMaterialsProtocol | None:
    """依赖解析源（Console `POST /internal/runtime/schedule-admission-materials`）。

    lifespan 注入的 `app.state.http_pools` 复用连接池；未进 lifespan 的直构/测试可显式
    注入 `app.state.schedule_materials_client`。两者都缺省返回 None —— **不在依赖层失败**：
    幂等重放不需要重新解析依赖，只有真正要做 Preflight 的调用方才显式拒绝。
    """
    injected: ScheduleMaterialsProtocol | None = getattr(
        request.app.state, "schedule_materials_client", None
    )
    if injected is not None:
        return injected
    pools = getattr(request.app.state, "http_pools", None)
    settings = SharedSettings()
    if pools is None or not settings.console_platform_url:
        return None
    return ConsoleResolveClient(
        settings.console_platform_url,
        client=pools.control,
        service_token=settings.internal_service_token,
    )


AgentId = Annotated[uuid.UUID | None, Depends(get_agent_id)]
RequiredAgentId = Annotated[uuid.UUID, Depends(require_agent_id)]
EntryChannel = Annotated[str | None, Depends(get_entry_channel)]


def get_confirmation_scope(
    surface: Annotated[str | None, Header(alias=CONFIRMATION_SURFACE_HEADER)] = None,
    ref: Annotated[str | None, Header(alias=CONFIRMATION_REF_HEADER)] = None,
) -> ConfirmScope | None:
    """用户确认动作的可信来源。

    缺任一项即「没有用户动作」：模型工具上下文没有这条通路，由应用层以
    `SCHEDULE_CONFIRMATION_REQUIRED` 拒绝，绝不由服务端替模型补一个「确认」。
    """
    surface_value = (surface or "").strip().upper()
    ref_value = (ref or "").strip()
    if not surface_value or not ref_value:
        return None
    try:
        return ConfirmScope(surface=ConfirmationSurface(surface_value), ref=ref_value)
    except ValueError as exc:
        raise AppError(ErrorCode.SCHEDULE_CONFIRMATION_REQUIRED) from exc


ConfirmationScope = Annotated[ConfirmScope | None, Depends(get_confirmation_scope)]
ScheduleMaterialsClientDep = Annotated[
    ScheduleMaterialsProtocol | None, Depends(get_schedule_materials_client)
]

def get_tool_operation_canceller(request: Request) -> ToolOperationCanceller | None:
    """远端 operation 取消端口（Runtime API-30）。

    lifespan 注入的 `app.state.http_pools` 复用业务出站池；测试可显式注入
    `app.state.tool_operation_canceller`。两者都缺省返回 None —— 取消意图已在事务内
    持久化（`REQUESTED` 证据），没有端口时保持「待核实」，绝不假装远端已回滚。
    """
    injected: ToolOperationCanceller | None = getattr(
        request.app.state, "tool_operation_canceller", None
    )
    if injected is not None:
        return injected
    pools = getattr(request.app.state, "http_pools", None)
    settings = SharedSettings()
    if pools is None or not settings.agent_runtime_url:
        return None
    return RuntimeToolOperationCanceller(
        settings.agent_runtime_url,
        pools.egress,
        service_token=settings.internal_service_token,
    )


ToolOperationCancellerDep = Annotated[
    ToolOperationCanceller | None, Depends(get_tool_operation_canceller)
]
