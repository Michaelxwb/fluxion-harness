"""Webchat 公开资源服务（design §3.4 API-05/06/08/09/11/14/15；TASK-052/070）。

职责与边界：

- **完整转 Owner**：入口/会话/消息/取消/恢复的每个事实都只从 Owner 取得 —— 主体与授权集合
  来自 Console（由 `ChatIdentityDep` 注入），Run/会话/历史/受理来自 Runtime 的受信内部端点。
  网关**不持库**（`tests/architecture/test_im_gateway_boundaries.py` 机检零数据库依赖），
  也不自建第二份状态：本服务只做授权判定、请求组装与公开投影校验。
- **门控先于幂等回执**：调用方（路由）先经 CHAT 主体门控，本服务再核**入口/Agent 当前授权**
  （`ChatIdentity.agents` 是 Console 每次核验的当前授权集合），之后才把 `Idempotency-Key`
  交给 Runtime —— 撤销/越权/上下文不符的请求在触达 Owner 的幂等面之前就被拒（E-05/E-12）。
- **受理与订阅解耦**：消息提交只等到 Owner 的**受理裁决**（首帧），长流不占 HTTP 处理器
  （RULE-01：HTTP 断开不取消执行；正文/阶段由公开 SSE 按 Run 身份另订）。
- **不编造事实**：202 的状态/阶段取自 Owner 的 Run 视图（持久阶段投影），201 的创建时不变式
  （`last_seq=0`/`active_run=None`）由 Owner 行事实校验；Owner 的坏封套/不可投影事实一律
  显式 `COMMON_INTERNAL_ERROR`，不回显内部内容。
- **凭据卫生**：本服务不接触 CHAT 会话原文；响应里没有主体、凭据或 Console 角色字段。

入口解析（API-09/15 的接线）：公共路径不带入口，服务先经 Runtime 的
`/internal/webchat/conversations/{id}/scope` 按 (tenant, user) 解析归属，再走**入口显式**的
读取端点（TASK-050 的核属语义不在网关侧重写）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import BotSnapshotItem, ChannelName, RunRequest, RunStatus
from muad_contracts.runtime import ChannelContext, MessageInput
from muad_contracts.webchat import (
    WebchatConversationCreated,
    WebchatConversationDetail,
    WebchatConversationPage,
    WebchatConversationScope,
    WebchatEntry,
    WebchatEntryPage,
    WebchatExecutionStatus,
    WebchatMessageAccepted,
    WebchatMessagePage,
    WebchatMessageRequest,
    WebchatResumeReceipt,
    WebchatResumeRequest,
    WebchatRunCancelResult,
)
from muad_contracts.webchat_events import (
    WebchatNotificationPage,
    WebchatNotificationReadReceipt,
)
from pydantic import BaseModel, ValidationError

from .chat_principal import ChatIdentity
from .runtime_client import RunAdmission

logger = logging.getLogger(__name__)

#: 本通道的公开入口词表（与 `BotSnapshotItem.channel` 同源；非本通道条目不进资源面）。
WEBCHAT_CHANNEL: ChannelName = "WEBCHAT"
#: 入口列表的页大小：条目数本身就是"当前授权入口"的完整集合（`WebchatPage` 要求 items≤page_size）。
MAX_ENTRIES = 100


class WebchatOwnerPort(Protocol):
    """资源接口要的 Owner 能力（Runtime 是唯一实现；结构替身只需实现同一动词面）。"""

    async def submit_run(
        self, request: RunRequest, *, tenant_id: str, idempotency_key: str
    ) -> RunAdmission: ...

    async def resume_run(
        self,
        run_id: UUID,
        payload: dict[str, Any],
        *,
        tenant_id: str,
        idempotency_key: str,
    ) -> RunAdmission: ...

    async def get_run(self, run_id: UUID, *, tenant_id: str) -> dict[str, Any]: ...

    async def cancel_run(self, run_id: UUID, *, tenant_id: str) -> dict[str, Any]: ...

    async def create_conversation(
        self,
        agent_id: UUID,
        platform_user_id: UUID,
        *,
        tenant_id: str = "",
        trace_id: str = "",
        idempotency_key: str | None = None,
        entry_id: UUID | None = None,
        external_thread_id: str = "",
    ) -> dict[str, Any]: ...

    async def list_conversations(
        self,
        entry_id: UUID,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID,
        page: int,
        page_size: int | None,
    ) -> dict[str, Any]: ...

    async def get_conversation(
        self,
        conversation_id: UUID,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID,
        entry_id: UUID,
    ) -> dict[str, Any]: ...

    async def list_messages(
        self,
        conversation_id: UUID,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID,
        entry_id: UUID,
        page: int,
        page_size: int | None,
        through_seq: int | None,
    ) -> dict[str, Any]: ...

    async def resolve_conversation_scope(
        self, conversation_id: UUID, *, tenant_id: str, user_id: UUID
    ) -> dict[str, Any]: ...

    async def list_notifications(
        self,
        user_id: UUID,
        *,
        tenant_id: str,
        after_seq: int,
        page: int,
        page_size: int | None,
    ) -> dict[str, Any]: ...

    async def mark_notification_read(
        self, notification_id: UUID, *, tenant_id: str, user_id: UUID
    ) -> dict[str, Any]: ...


class WebchatEntrySource(Protocol):
    """入口快照的只读面（生产实现是 `BotSnapshotCache`，装配根按同形发布）。"""

    @property
    def items(self) -> tuple[BotSnapshotItem, ...]: ...


def _project[ContractT: BaseModel](model: type[ContractT], data: dict[str, Any]) -> ContractT:
    """Owner 封套 → 公开契约；字段不符显式失败（不回显内部内容）。"""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        logger.warning("webchat_owner_projection_failed model=%s", model.__name__)
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc


def required_uuid(data: dict[str, Any], field: str) -> UUID:
    """Owner 封套里的必填 UUID：坏值显式失败（不回显内部内容）。"""
    raw = data.get(field)
    try:
        return UUID(str(raw))
    except (ValueError, TypeError) as exc:
        logger.warning("webchat_owner_field_invalid field=%s", field)
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc


def owned_run_state(identity: ChatIdentity, state: dict[str, Any]) -> dict[str, Any]:
    """Run 归属核验：他主体的 Run 视同不存在（404 不泄露存在性；资源面/SSE 同口径）。"""
    if str(state.get("user_id")) != str(identity.principal.platform_user_id):
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    return state


class WebchatResourceService:
    """API-05/06/08/09/14/15 的应用服务：授权判定 + 请求组装 + 公开投影校验。"""

    def __init__(
        self, *, runtime: WebchatOwnerPort, entries: WebchatEntrySource, tenant_id: str
    ) -> None:
        self._runtime = runtime
        self._entries = entries
        self._tenant_id = tenant_id

    # --- API-05 ---------------------------------------------------------------------
    def list_entries(self, identity: ChatIdentity) -> WebchatEntryPage:
        """仅列已启用且当前用户获 Agent 授权的 WEBCHAT 入口；授权来自本次 Console 核验。"""
        authorized = {agent.agent_id: agent.name for agent in identity.agents}
        items = [
            WebchatEntry(
                entry_id=item.bot_account_id,
                agent_id=item.agent_id,
                display_name=authorized[item.agent_id],
            )
            for item in self._entries.items
            if item.enabled and item.channel == WEBCHAT_CHANNEL and item.agent_id in authorized
        ]
        if len(items) > MAX_ENTRIES:
            # 入口数是"当前授权入口"的完整集合，不允许静默截断（截断=看不见的入口）。
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return WebchatEntryPage(
            items=items, page=1, page_size=max(len(items), 1), total=len(items)
        )

    # --- API-06 ---------------------------------------------------------------------
    async def submit_message(
        self,
        identity: ChatIdentity,
        entry_id: UUID,
        request: WebchatMessageRequest,
        *,
        idempotency_key: str,
    ) -> WebchatMessageAccepted:
        """提交消息（202）：受理裁决后立即返回；Run 状态/阶段从 Owner 视图投影。"""
        ref = self._resolve_entry(identity, entry_id)
        principal = identity.principal
        run_request = RunRequest(
            agent_id=ref.agent_id,
            platform_user_id=principal.platform_user_id,
            conversation_id=request.conversation_id,
            channel=ChannelContext(
                type=WEBCHAT_CHANNEL,
                bot_id=str(ref.entry_id),
                external_user_id=str(principal.platform_user_id),
                external_conversation_id=str(request.conversation_id),
            ),
            message=MessageInput(
                id=request.message_id,
                type="attachment" if request.upload_ids else "text",
                text=request.text,
                upload_ids=list(request.upload_ids),
            ),
        )
        admission = await self._runtime.submit_run(
            run_request, tenant_id=self._tenant_id, idempotency_key=idempotency_key
        )
        if admission.conversation_id is not None and (
            admission.conversation_id != request.conversation_id
        ):
            # 受理的 Run 必须落在请求的显式会话上；不一致是路由错误，显式失败不糊过去。
            logger.warning("webchat_admission_conversation_mismatch")
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        state = await self._owned_run(identity, admission.run_id)
        return WebchatMessageAccepted(
            conversation_id=required_uuid(state, "conversation_id"),
            run_id=admission.run_id,
            status=_run_status(state),
            execution_status=_execution_status(state),
        )

    # --- API-08 ---------------------------------------------------------------------
    async def create_conversation(
        self, identity: ChatIdentity, entry_id: UUID, *, idempotency_key: str
    ) -> WebchatConversationCreated:
        """新建会话（201）：入口/主体由服务端持有，创建时不变式由 Owner 行事实校验。"""
        ref = self._resolve_entry(identity, entry_id)
        principal = identity.principal
        data = await self._runtime.create_conversation(
            ref.agent_id,
            principal.platform_user_id,
            tenant_id=self._tenant_id,
            idempotency_key=idempotency_key,
            entry_id=ref.entry_id,
            external_thread_id=str(principal.platform_user_id),
        )
        if data.get("status") != "ACTIVE" or data.get("last_seq") != 0:
            # 创建回执只描述创建时事实；与行事实不符是坏封套，显式失败不伪造。
            logger.warning("webchat_conversation_create_shape_invalid")
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return WebchatConversationCreated(
            conversation_id=required_uuid(data, "conversation_id"),
            entry_id=required_uuid(data, "entry_id"),
            title=str(data.get("title") or ""),
            status="ACTIVE",
            last_seq=0,
            active_run=None,
        )

    async def cancel_run(self, identity: ChatIdentity, run_id: UUID) -> WebchatRunCancelResult:
        """精确取消（可重放）：先核原 Run 归属，再按原 Run 转 Owner。"""
        await self._owned_run(identity, run_id)
        data = await self._runtime.cancel_run(run_id, tenant_id=self._tenant_id)
        return _project(WebchatRunCancelResult, data)

    async def resume_run(
        self, identity: ChatIdentity, run_id: UUID, request: WebchatResumeRequest
    ) -> WebchatResumeReceipt:
        """待确认恢复：显式绑定原 Run/submission/source；回执取恢复后的真实阶段。"""
        await self._owned_run(identity, run_id)
        principal = identity.principal
        admission = await self._runtime.resume_run(
            run_id,
            {
                "platform_user_id": str(principal.platform_user_id),
                "input": {
                    "id": request.message_id,
                    "type": "text",
                    "text": request.selection,
                },
                "submission_id": str(request.submission_id),
                "source_event_id": str(request.source_event_id),
            },
            tenant_id=self._tenant_id,
            idempotency_key=request.message_id,
        )
        state = await self._owned_run(identity, admission.run_id)
        return WebchatResumeReceipt(
            run_id=admission.run_id,
            submission_id=request.submission_id,
            status=_run_status(state),
            execution_status=_execution_status(state),
        )

    # --- API-09/14/15 ---------------------------------------------------------------
    async def list_conversations(
        self, identity: ChatIdentity, entry_id: UUID, *, page: int, page_size: int | None
    ) -> WebchatConversationPage:
        """API-14：入口会话列表（Owner 的公开投影原样校验，不重排/不补字段）。"""
        ref = self._resolve_entry(identity, entry_id)
        data = await self._runtime.list_conversations(
            ref.entry_id,
            tenant_id=self._tenant_id,
            user_id=identity.principal.platform_user_id,
            agent_id=ref.agent_id,
            page=page,
            page_size=page_size,
        )
        return _project(WebchatConversationPage, data)

    async def get_conversation(
        self, identity: ChatIdentity, conversation_id: UUID
    ) -> WebchatConversationDetail:
        """API-15：解析归属（含当前授权）后走入口显式详情端点。"""
        scope = await self._resolve_scope(identity, conversation_id)
        data = await self._runtime.get_conversation(
            conversation_id,
            tenant_id=self._tenant_id,
            user_id=identity.principal.platform_user_id,
            agent_id=scope.agent_id,
            entry_id=scope.entry_id,
        )
        return _project(WebchatConversationDetail, data)

    async def list_messages(
        self,
        identity: ChatIdentity,
        conversation_id: UUID,
        *,
        page: int,
        page_size: int | None,
        through_seq: int | None,
    ) -> WebchatMessagePage:
        """API-09：解析归属后走入口显式消息端点（固定水位分页由 Owner 裁决）。"""
        scope = await self._resolve_scope(identity, conversation_id)
        data = await self._runtime.list_messages(
            conversation_id,
            tenant_id=self._tenant_id,
            user_id=identity.principal.platform_user_id,
            agent_id=scope.agent_id,
            entry_id=scope.entry_id,
            page=page,
            page_size=page_size,
            through_seq=through_seq,
        )
        return _project(WebchatMessagePage, data)

    # --- API-11 ---------------------------------------------------------------------
    async def list_notifications(
        self,
        identity: ChatIdentity,
        *,
        after_seq: int,
        page: int,
        page_size: int | None,
    ) -> WebchatNotificationPage:
        """API-11：按用户 feed seq 增量分页（主体就是本次 Console 核验的用户）。

        通知是**用户级**事实（跨会话/入口），所以没有入口/Agent 维度：受信调用只携带已验证
        `platform_user_id`，浏览器自报字段没有位置；游标（含 `CURSOR_AHEAD` 的真实水位）由
        Owner 裁决、原样投影，网关不在内存里建第二份通知权威。
        """
        data = await self._runtime.list_notifications(
            identity.principal.platform_user_id,
            tenant_id=self._tenant_id,
            after_seq=after_seq,
            page=page,
            page_size=page_size,
        )
        return _project(WebchatNotificationPage, data)

    async def mark_notification_read(
        self, identity: ChatIdentity, notification_id: UUID
    ) -> WebchatNotificationReadReceipt:
        """API-11：显式已读（幂等；只标记明确读过且属于本主体的行，他主体 404 不泄露存在性）。

        已读与送达是两条独立事实：本服务不触碰投递状态，回执只有身份与持久 `read_at`。
        """
        data = await self._runtime.mark_notification_read(
            notification_id,
            tenant_id=self._tenant_id,
            user_id=identity.principal.platform_user_id,
        )
        return _project(WebchatNotificationReadReceipt, data)

    # --- 内部：授权与归属 -------------------------------------------------------------
    def _resolve_entry(self, identity: ChatIdentity, entry_id: UUID) -> ResolvedEntry:
        return resolve_authorized_entry(self._entries, identity, entry_id)

    async def _resolve_scope(
        self, identity: ChatIdentity, conversation_id: UUID
    ) -> WebchatConversationScope:
        """公共路径 → 会话归属；会话的 Agent 必须在**当前**授权集合内（撤销影响后续访问）。"""
        data = await self._runtime.resolve_conversation_scope(
            conversation_id,
            tenant_id=self._tenant_id,
            user_id=identity.principal.platform_user_id,
        )
        scope = _project(WebchatConversationScope, data)
        if scope.agent_id not in {agent.agent_id for agent in identity.agents}:
            raise AppError(ErrorCode.FORBIDDEN)
        return scope

    async def _owned_run(self, identity: ChatIdentity, run_id: UUID) -> dict[str, Any]:
        """Run 归属核验（共用口径）：他主体的 Run 视同不存在（404 不泄露存在性）。"""
        return owned_run_state(
            identity, await self._runtime.get_run(run_id, tenant_id=self._tenant_id)
        )


@dataclass(frozen=True, slots=True)
class ResolvedEntry:
    """入口解析结论：公开 entry_id 就是 Runtime 可归属的入口账号 UUID。"""

    entry_id: UUID
    agent_id: UUID


def resolve_authorized_entry(
    entries: WebchatEntrySource, identity: ChatIdentity, entry_id: UUID
) -> ResolvedEntry:
    """入口 → (agent, entry)：未启用/非本通道/未知一律 404，未授权 Agent 越权 403。

    入口事实只来自入口快照、授权只来自本次 Console 核验的授权集合：入口显式读写面与上传面
    共用这一条裁决，不复制第二份判定。
    """
    item = next(
        (
            candidate
            for candidate in entries.items
            if candidate.bot_account_id == entry_id
            and candidate.enabled
            and candidate.channel == WEBCHAT_CHANNEL
        ),
        None,
    )
    if item is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    if item.agent_id not in {agent.agent_id for agent in identity.agents}:
        raise AppError(ErrorCode.FORBIDDEN)
    return ResolvedEntry(entry_id=entry_id, agent_id=item.agent_id)


def _run_status(state: dict[str, Any]) -> RunStatus:
    """Run 视图 → 契约状态；`CANCELLING` 是展示态、不是持久状态，坏封套显式失败。"""
    try:
        return RunStatus(str(state.get("status")))
    except ValueError as exc:
        logger.warning("webchat_run_status_invalid")
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc


def _execution_status(state: dict[str, Any]) -> WebchatExecutionStatus:
    return _project(WebchatExecutionStatus, state.get("execution_status") or {})
