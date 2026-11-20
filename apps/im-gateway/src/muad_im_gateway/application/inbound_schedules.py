"""聊天入口的 Schedule 用户动作：Draft 确认与 Human 回答分流（design §3.2.6、§3.2.8、§3.4.4）。

两条边界：

- **确认**：用户在聊天里显式回复 `/confirm <draft_id>`（或由卡片按钮解析出同一动作）。Gateway
  用 Console 已核验的 actor 读出 Draft 的 **服务端** `draft_hash`，再以
  `surface=入口通道`、`ref=入站消息 id` 代理 API-31 → API-27。客户端自报 hash 没有入口，
  模型工具上下文也没有这条通路，所以"模型同轮自确认"在协议层不可达。
- **回答**：入站文本先问 Runtime「这是不是某个待回答问题的回答」。Runtime 按
  `user+agent+这条消息的来源会话` 匹配 OPEN 提问；Gateway 只把结论变成用户可见文本，
  不写 Execution，也不为已经受理的回复新建 Run。

契约形状：

- `ScheduleOwnerPort` 由 Agent Worker 的受信内部面实现（`WorkerClient`）；
- `HumanRoutePort` 由 Runtime 的会话路由端点实现（`RuntimeClient`）。
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    ChannelHumanRouteRequest,
    ChannelHumanRouteResponse,
    ConfirmationSurface,
)
from muad_logging import get_logger
from pydantic import ValidationError

logger = get_logger(__name__)


class ScheduleOwnerPort(Protocol):
    """Draft 事实与确认动作的 Owner 面（Agent Worker）。"""

    async def get_schedule_draft(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID,
        draft_id: UUID,
    ) -> dict[str, Any]: ...

    async def confirm_draft(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        draft_id: UUID,
        payload: dict[str, Any],
        surface: ConfirmationSurface,
        ref: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...


class HumanRoutePort(Protocol):
    """Human 回答路由的裁决面（Agent Runtime）。"""

    async def resolve_human_route(
        self, request: ChannelHumanRouteRequest, *, tenant_id: str, trace_id: str = ""
    ) -> dict[str, Any]: ...


def confirmation_surface(channel: str) -> ConfirmationSurface | None:
    """入口通道能否作确认来源；未登记的 surface 显式不支持（不猜、不复用别的来源）。"""
    try:
        return ConfirmationSurface(channel)
    except ValueError:
        return None


class ChatScheduleService:
    """聊天入口的 Schedule 动作；身份与租户只来自调用方已核验的主体。"""

    def __init__(
        self, *, owner: ScheduleOwnerPort, routes: HumanRoutePort, tenant_id: str
    ) -> None:
        self._owner = owner
        self._routes = routes
        self._tenant_id = tenant_id

    async def confirm(
        self,
        *,
        user_id: UUID,
        agent_id: UUID,
        channel: str,
        draft_id: UUID,
        message_id: str,
    ) -> dict[str, Any]:
        """按服务端读到的 hash 确认；同一条入站消息重投 = 同一个幂等键（不重复生效）。"""
        surface = confirmation_surface(channel)
        if surface is None:
            raise AppError(
                ErrorCode.SCHEDULE_CONFIRMATION_REQUIRED,
                data={"reason": "CONFIRM_SURFACE_UNSUPPORTED"},
            )
        draft = await self._owner.get_schedule_draft(
            tenant_id=self._tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            draft_id=draft_id,
        )
        draft_hash = draft.get("draft_hash")
        if not isinstance(draft_hash, str) or not draft_hash:
            logger.warning("chat_confirm_draft_without_hash draft_id=%s", draft_id)
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return await self._owner.confirm_draft(
            tenant_id=self._tenant_id,
            user_id=user_id,
            draft_id=draft_id,
            payload={"draft_hash": draft_hash},
            surface=surface,
            ref=message_id,
            idempotency_key=message_id,
        )

    async def route_human(
        self, request: ChannelHumanRouteRequest, *, trace_id: str = ""
    ) -> ChannelHumanRouteResponse:
        """把 Runtime 的裁决投影成契约；形状不符显式失败，不猜结果。"""
        data = await self._routes.resolve_human_route(
            request, tenant_id=self._tenant_id, trace_id=trace_id
        )
        try:
            return ChannelHumanRouteResponse.model_validate(data)
        except ValidationError as exc:
            logger.warning("chat_human_route_projection_failed")
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc


__all__ = [
    "ChatScheduleService",
    "HumanRoutePort",
    "ScheduleOwnerPort",
    "confirmation_surface",
]
