"""聊天入口的一条文本消息 → Human 回答 / 澄清 / 普通聊天（design §3.2.6、§3.5.8）。

三条硬边界：

1. **按会话来源匹配，不按文本猜**：候选集是 Owner 给的"这个 user+agent 在这条入站消息的
   投递目标上仍 OPEN 的提问"。文本内容只作为回答载荷，绝不参与选择回答哪一个。
2. **恰好一个才回答**：多个 OPEN 时列出来让用户指定（`CLARIFY`），零个才回到普通聊天
   （`NONE`）；同一 Execution 至多一个 OPEN，多个必然属于不同任务。
3. **回答失败不改变状态**：Owner 的 CAS/时效/格式错误原样翻译成 `REJECTED`，既不建 Run，
   也不伪造"已受理"。

同源消息重投的幂等身份是入站消息 id：Owner 的回答台账按它重放首次结果，Runtime 不另造
第二套去重。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    ChannelHumanRouteRequest,
    ChannelHumanRouteResponse,
    HumanRouteCandidate,
    HumanRouteOutcome,
    PendingPage,
)
from muad_logging import get_logger
from pydantic import ValidationError

logger = get_logger(__name__)

#: 无结构化 schema 的提问接受自由文本：回答载荷就是用户这条消息本身。
FREE_TEXT_RESPONSE_KEY = "text"


class HumanRouteOwnerPort(Protocol):
    """提问的所有者面（Agent Worker 是唯一实现；结构替身只需实现同一动词面）。"""

    async def list_open_human_interactions(
        self,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        agent_id: uuid.UUID,
        channel: str,
        bot_id: str,
        external_user_id: str | None,
        external_conversation_id: str | None,
        trace_id: str = "",
    ) -> dict[str, Any]: ...

    async def respond_human(
        self,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        agent_id: uuid.UUID,
        interaction_id: uuid.UUID,
        response: Mapping[str, Any],
        idempotency_key: str,
        trace_id: str = "",
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class HumanRouteDecision:
    """一次路由裁决：`NONE` 是唯一允许调用方继续新建 Run 的结论。"""

    outcome: HumanRouteOutcome
    interaction_id: uuid.UUID | None = None
    candidates: tuple[HumanRouteCandidate, ...] = ()
    reason: str | None = None

    def as_response(self) -> ChannelHumanRouteResponse:
        return ChannelHumanRouteResponse(
            outcome=self.outcome,
            interaction_id=self.interaction_id,
            candidates=list(self.candidates),
            reason=self.reason,
        )


class HumanRoutingService:
    """把一条入站消息按"会话里待回答的问题"分流；不写任何状态以外的东西。"""

    def __init__(self, owner: HumanRouteOwnerPort) -> None:
        self._owner = owner

    async def route(
        self, request: ChannelHumanRouteRequest, *, tenant_id: str, trace_id: str = ""
    ) -> HumanRouteDecision:
        pending = await self._pending(request, tenant_id=tenant_id, trace_id=trace_id)
        humans = [item for item in pending.items if item.kind == "HUMAN"]
        if not humans:
            return HumanRouteDecision(HumanRouteOutcome.NONE)
        if len(humans) > 1:
            # 绝不按新旧挑一个：列出候选，由用户指定要回答哪一个任务。
            return HumanRouteDecision(
                HumanRouteOutcome.CLARIFY,
                candidates=tuple(_candidate(item) for item in humans),
            )
        return await self._answer(humans[0].interaction_id, request, tenant_id, trace_id)

    async def _pending(
        self, request: ChannelHumanRouteRequest, *, tenant_id: str, trace_id: str
    ) -> PendingPage:
        data = await self._owner.list_open_human_interactions(
            tenant_id=tenant_id,
            actor_user_id=request.platform_user_id,
            agent_id=request.agent_id,
            channel=request.channel,
            bot_id=request.bot_id,
            external_user_id=request.external_user_id,
            external_conversation_id=request.external_conversation_id,
            trace_id=trace_id,
        )
        try:
            return PendingPage.model_validate(data)
        except ValidationError as exc:
            logger.warning("human_route_pending_projection_failed")
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc

    async def _answer(
        self,
        interaction_id: uuid.UUID,
        request: ChannelHumanRouteRequest,
        tenant_id: str,
        trace_id: str,
    ) -> HumanRouteDecision:
        try:
            await self._owner.respond_human(
                tenant_id=tenant_id,
                actor_user_id=request.platform_user_id,
                agent_id=request.agent_id,
                interaction_id=interaction_id,
                response={FREE_TEXT_RESPONSE_KEY: request.text},
                # 同源消息重投 = 同一个幂等键：Owner 重放首次结果，不会回答两次。
                idempotency_key=request.message_id,
                trace_id=trace_id,
            )
        except AppError as exc:
            logger.info(
                "human_route_answer_rejected",
                extra={"interaction_id": str(interaction_id), "code": exc.code},
            )
            return HumanRouteDecision(
                HumanRouteOutcome.REJECTED,
                interaction_id=interaction_id,
                reason=_reject_reason(exc),
            )
        return HumanRouteDecision(HumanRouteOutcome.ANSWERED, interaction_id=interaction_id)


def _candidate(item: Any) -> HumanRouteCandidate:
    """候选条目只取可读事实（任务名/问题/截止），不泄露内部载荷。"""
    return HumanRouteCandidate(
        interaction_id=item.interaction_id,
        schedule_id=item.schedule_id,
        execution_id=item.execution_id,
        schedule_name=item.schedule_name,
        question=item.question,
        deadline_at=item.deadline_at,
    )


def _reject_reason(error: AppError) -> str:
    """稳定原因码：优先用 Owner 给的细分 reason，否则退回错误码本身。"""
    data = error.data if isinstance(error.data, Mapping) else {}
    reason = data.get("reason")
    return str(reason) if isinstance(reason, str) and reason else str(error.code)


__all__ = [
    "FREE_TEXT_RESPONSE_KEY",
    "HumanRouteDecision",
    "HumanRouteOwnerPort",
    "HumanRoutingService",
]
