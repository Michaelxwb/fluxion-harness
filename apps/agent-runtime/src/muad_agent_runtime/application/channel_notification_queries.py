"""活动收件箱：按 feed seq 增量读取、显式已读与受信 Task 结果入箱（design §3.4 API-11、§3.6）。

职责与边界：

- **增量读取**：按 `(tenant,user)` feed 的 seq 水位分页 —— `after_seq` 是客户端已应用的水位，
  `through_seq` 是本次扫描的权威 feed 水位，`next_seq` 是下一次增量读取的起点（本页已追平水位
  时等于 `through_seq`）；游标超前（超过 feed 水位，例如跨用户/跨流的游标）显式
  `CURSOR_AHEAD`，不静默夹取成空页。
- **已读与送达是两条独立事实**（design §3.6）：`read_at` 只由显式已读改变；`delivery_state`/
  租约/投递结果由交付路径（TASK-065/066）改变。标记已读不碰投递回执，送达不代替已读，已读不
  要求送达；重复标记幂等（返回原 `read_at`）；只标记明确读过的行，其他会话/其他行不受影响。
- **受信 Task 结果入箱**：核真实 `ToolResultInbox`/`ToolOperation`/Run/用户/会话入口归属与
  artifact 的 Task 来源（跨用户/伪造来源/无入口归属一律拒绝）；写入走 `write_run_notification`
  同一接缝（feed 锁内分配 seq、稳定 `delivery_key` 重投命中原回执）；调用方提交后才返回
  `persisted=True`（"持久入箱后才回 persisted"）。
- **安全投影**：DTO 只含安全摘要/引用；`delivery_state`/`delivery_key`/租约/投递结果/
  `storage_key`/载荷永不进入。附件复用历史/SSE 的同一安全投影（`project_attachment`）。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import MAX_PAGE_SIZE
from muad_contracts.webchat import WebchatAttachment
from muad_contracts.webchat_events import (
    WebchatNotification,
    WebchatNotificationPage,
    WebchatNotificationReadReceipt,
    WebchatTaskResultReceipt,
    WebchatTaskResultRegistration,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db_clock import database_now
from ..infrastructure.models.async_tools import ToolOperation, ToolResultInbox
from ..infrastructure.models.runtime import (
    Artifact,
    ChannelNotification,
    ChannelNotificationFeed,
    Conversation,
    RunRecord,
)
from .channel_notifications import (
    KIND_RUN_COMPLETED,
    KIND_RUN_WAITING_INPUT,
    KIND_TASK_RESULT,
    NotificationKind,
    RunNotificationReceipt,
    task_result_delivery_key,
    write_run_notification,
)
from .public_events import project_attachment

#: kind → 面向用户的安全摘要（读取层投影，不落列：行内事实不承载文案）。
KIND_SUMMARIES: dict[NotificationKind, str] = {
    KIND_RUN_COMPLETED: "会话有新结果",
    KIND_RUN_WAITING_INPUT: "有新的待确认",
    KIND_TASK_RESULT: "后台任务已完成",
}


@dataclass(frozen=True, slots=True)
class NotificationScope:
    """活动收件箱的核属维度：feed 与通知都是**用户级**事实（跨会话/入口，不按会话切分）。"""

    tenant_id: str
    user_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class VerifiedTaskSource:
    """核验通过的 Task 结果来源：真实 Run 行 + 受理时留下的 canonical 回执事件。"""

    run: RunRecord
    receipt_event_id: uuid.UUID


def _validate_page(page: int, page_size: int) -> None:
    if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)


def _projected_kind(raw: str) -> NotificationKind:
    """行内 kind → 契约字面量；未知值显式失败（不把未知通知当普通文案暴露）。"""
    if raw not in KIND_SUMMARIES:
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return raw


class ChannelNotificationQueryService:
    """活动收件箱读取与已读（读取路径只写 `read_at`）+ 受信 Task 结果入箱。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----------------------------------------------------------------------------------
    # 增量读取
    # ----------------------------------------------------------------------------------

    async def list_notifications(
        self,
        scope: NotificationScope,
        *,
        after_seq: int,
        page: int,
        page_size: int,
    ) -> WebchatNotificationPage:
        """API-11：按用户 feed seq 增量分页（无跳过；游标超前显式失败）。"""
        _validate_page(page, page_size)
        through_seq = await self._through_seq(scope)
        if after_seq > through_seq:
            raise AppError(
                ErrorCode.CURSOR_AHEAD,
                data={"after_seq": after_seq, "through_seq": through_seq},
            )
        if after_seq == through_seq:
            # 空闲轮询（多数 15s 轮询的稳态）：没有新行就不做 count/page 两条查询；
            # 未读徽标仍是用户可见事实，保留一次轻量 count。
            return WebchatNotificationPage(
                items=[],
                page=page,
                page_size=page_size,
                total=0,
                through_seq=through_seq,
                next_seq=through_seq,
                unread_count=await self._unread_count(scope),
            )
        rows, total = await self._page(scope, after_seq, through_seq, page=page, page_size=page_size)
        items = await self._project(scope, rows)
        return WebchatNotificationPage(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            through_seq=through_seq,
            next_seq=self._next_seq(
                through_seq=through_seq,
                rows=rows,
                total=total,
                page=page,
                page_size=page_size,
            ),
            unread_count=await self._unread_count(scope),
        )

    async def _through_seq(self, scope: NotificationScope) -> int:
        """权威 feed 水位：没有 feed 行即 0（首次读取；不注册行、不写任何状态）。"""
        last = await self._session.scalar(
            sa.select(ChannelNotificationFeed.last_seq).where(
                ChannelNotificationFeed.tenant_id == scope.tenant_id,
                ChannelNotificationFeed.user_id == scope.user_id,
            )
        )
        return 0 if last is None else int(last)

    async def _page(
        self,
        scope: NotificationScope,
        after_seq: int,
        through_seq: int,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[ChannelNotification], int]:
        """窗口 `(after_seq, through_seq]` 的可见行与窗口总数。

        **先 count 再取行**：`through_seq` 一经读出，窗口内不可能再出现新可见行（seq 在 feed
        行锁内分配、与通知行同事务提交）；此后可见集合只会因隐藏而缩小 ⇒ `len(rows) <= total`
        恒成立（契约 `WebchatPage` 的页边界不变量）。
        """
        conditions = [
            ChannelNotification.tenant_id == scope.tenant_id,
            ChannelNotification.user_id == scope.user_id,
            ChannelNotification.seq > after_seq,
            ChannelNotification.seq <= through_seq,
            ChannelNotification.is_deleted.is_(False),
        ]
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(ChannelNotification).where(*conditions)
        )
        rows = list(
            await self._session.scalars(
                sa.select(ChannelNotification)
                .where(*conditions)
                .order_by(ChannelNotification.seq)
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        )
        return rows, int(total or 0)

    @staticmethod
    def _next_seq(
        *,
        through_seq: int,
        rows: Sequence[ChannelNotification],
        total: int,
        page: int,
        page_size: int,
    ) -> int:
        """下一次增量读取的水位：本页已扫到窗口末尾即 feed 水位，否则本页最后一行。"""
        scanned = (page - 1) * page_size + len(rows)
        if rows and scanned < total:
            return int(rows[-1].seq)
        return through_seq

    async def _unread_count(self, scope: NotificationScope) -> int:
        """用户级未读徽标：只统计可见行（隐藏通知不再打扰用户，但保留投递回执）。"""
        count = await self._session.scalar(
            sa.select(sa.func.count())
            .select_from(ChannelNotification)
            .where(
                ChannelNotification.tenant_id == scope.tenant_id,
                ChannelNotification.user_id == scope.user_id,
                ChannelNotification.read_at.is_(None),
                ChannelNotification.is_deleted.is_(False),
            )
        )
        return int(count or 0)

    async def _project(
        self, scope: NotificationScope, rows: Sequence[ChannelNotification]
    ) -> list[WebchatNotification]:
        """行 → 安全公开 DTO：批量取会话（agent_id）与附件（同历史/SSE 的附件投影）。"""
        if not rows:
            return []
        conversations = await self._conversations(scope, rows)
        artifacts = await self._artifacts(scope, rows)
        items: list[WebchatNotification] = []
        for row in rows:
            conversation = conversations.get(row.conversation_id)
            if conversation is None:
                # FK 保证会话行存在；缺行是数据异常，显式失败不投影半条通知。
                raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
            kind = _projected_kind(row.kind)
            items.append(
                WebchatNotification(
                    id=row.id,
                    seq=int(row.seq),
                    entry_id=row.entry_id,
                    agent_id=conversation.agent_id,
                    conversation_id=row.conversation_id,
                    run_id=row.run_id,
                    task_id=row.task_id,
                    event_id=row.event_id,
                    kind=kind,
                    summary=KIND_SUMMARIES[kind],
                    attachments=self._attachments(row.artifact_ids, artifacts),
                    created_at=row.create_time,
                    read_at=row.read_at,
                )
            )
        return items

    async def _conversations(
        self, scope: NotificationScope, rows: Sequence[ChannelNotification]
    ) -> dict[uuid.UUID, Conversation]:
        ids = {row.conversation_id for row in rows}
        found = await self._session.scalars(
            sa.select(Conversation).where(
                Conversation.tenant_id == scope.tenant_id, Conversation.id.in_(ids)
            )
        )
        return {conversation.id: conversation for conversation in found}

    async def _artifacts(
        self, scope: NotificationScope, rows: Sequence[ChannelNotification]
    ) -> dict[uuid.UUID, Artifact]:
        ids = {artifact_id for row in rows for artifact_id in row.artifact_ids}
        if not ids:
            return {}
        found = await self._session.scalars(
            sa.select(Artifact).where(
                Artifact.tenant_id == scope.tenant_id, Artifact.id.in_(ids)
            )
        )
        return {artifact.id: artifact for artifact in found}

    @staticmethod
    def _attachments(
        artifact_ids: Sequence[uuid.UUID], artifacts: dict[uuid.UUID, Artifact]
    ) -> list[WebchatAttachment]:
        projected: list[WebchatAttachment] = []
        for artifact_id in artifact_ids:
            attachment = project_attachment(artifact_id, artifacts)
            if attachment is not None:
                projected.append(attachment)
        return projected

    # ----------------------------------------------------------------------------------
    # 显式已读
    # ----------------------------------------------------------------------------------

    async def mark_read(
        self, scope: NotificationScope, notification_id: uuid.UUID
    ) -> WebchatNotificationReadReceipt:
        """显式标记一条通知已读：幂等、只影响明确读过的行，不碰投递状态/feed 水位。"""
        row = await self._session.scalar(
            sa.select(ChannelNotification)
            .where(
                ChannelNotification.id == notification_id,
                ChannelNotification.tenant_id == scope.tenant_id,
                ChannelNotification.user_id == scope.user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or row.is_deleted:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        if row.read_at is None:
            # 行锁后取 PG 服务端时间：跨实例已读时间同一时钟域（design §3.5 时间）。
            now = await database_now(self._session)
            row.read_at, row.update_time = now, now
            await self._session.flush()
        return WebchatNotificationReadReceipt(id=row.id, read_at=row.read_at)

    # ----------------------------------------------------------------------------------
    # 受信 Task 结果入箱
    # ----------------------------------------------------------------------------------

    async def register_task_result(
        self,
        *,
        tenant_id: str,
        registration: WebchatTaskResultRegistration,
    ) -> WebchatTaskResultReceipt:
        """受信 Task 结果入箱：核真实来源 → 持久写入 → 回 `persisted` 回执（调用方提交）。

        重投（同 Task）命中 `(tenant,delivery_key)` 唯一回执：不新占 seq、不新增未读。
        """
        source = await self._verified_task_source(tenant_id, registration)
        artifact_ids = await self._verified_artifacts(tenant_id, registration)
        receipt = await write_run_notification(
            self._session,
            run=source.run,
            kind=KIND_TASK_RESULT,
            delivery_key=task_result_delivery_key(registration.task_id),
            event_id=source.receipt_event_id,
            task_id=registration.task_id,
            artifact_ids=artifact_ids,
        )
        if receipt is None:
            # 无入口归属的会话不进网页收件箱（不编造入口身份）。
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return self._receipt(registration.task_id, receipt)

    @staticmethod
    def _receipt(task_id: uuid.UUID, receipt: RunNotificationReceipt) -> WebchatTaskResultReceipt:
        return WebchatTaskResultReceipt(
            task_id=task_id,
            notification_id=receipt.id,
            seq=receipt.seq,
            persisted=True,
            duplicate=not receipt.created,
        )

    async def _verified_task_source(
        self, tenant_id: str, registration: WebchatTaskResultRegistration
    ) -> VerifiedTaskSource:
        """核真实 Task/Run/用户/入口来源：受理结果、操作、Run、会话入口归属同时成立。

        `ToolResultInbox` 是结果已受理的持久证据（结果先经既有受信入口落库，才可能有入箱请求）；
        它同时给出 canonical 回执事件作为通知的 `event_id` 引用。任一步不成立即显式
        `TOOL_RESULT_BINDING_MISMATCH`（跨用户/伪造来源不得注入他人收件箱）。
        """
        inbox = await self._accepted_result(tenant_id, registration.task_id)
        await self._verify_operation(tenant_id, inbox, registration)
        run = await self._verify_run(tenant_id, inbox, registration)
        await self._verify_entry_attribution(tenant_id, run, registration.user_id)
        return VerifiedTaskSource(run=run, receipt_event_id=inbox.receipt_event_id)

    async def _accepted_result(self, tenant_id: str, task_id: uuid.UUID) -> ToolResultInbox:
        inbox = await self._session.scalar(
            sa.select(ToolResultInbox).where(
                ToolResultInbox.tenant_id == tenant_id,
                ToolResultInbox.task_id == task_id,
                ToolResultInbox.is_deleted.is_(False),
            )
        )
        if inbox is None:
            raise AppError(ErrorCode.TOOL_RESULT_BINDING_MISMATCH)
        return inbox

    async def _verify_operation(
        self,
        tenant_id: str,
        inbox: ToolResultInbox,
        registration: WebchatTaskResultRegistration,
    ) -> None:
        operation = await self._session.scalar(
            sa.select(ToolOperation).where(
                ToolOperation.id == inbox.operation_id,
                ToolOperation.tenant_id == tenant_id,
                ToolOperation.is_deleted.is_(False),
            )
        )
        if (
            operation is None
            or operation.task_id != registration.task_id
            or operation.run_id != inbox.run_id
            or operation.actor_user_id != registration.user_id
        ):
            raise AppError(ErrorCode.TOOL_RESULT_BINDING_MISMATCH)

    async def _verify_run(
        self,
        tenant_id: str,
        inbox: ToolResultInbox,
        registration: WebchatTaskResultRegistration,
    ) -> RunRecord:
        run = await self._session.scalar(
            sa.select(RunRecord).where(
                RunRecord.id == inbox.run_id,
                RunRecord.tenant_id == tenant_id,
                RunRecord.is_deleted.is_(False),
            )
        )
        if run is None or run.id != registration.run_id or run.user_id != registration.user_id:
            raise AppError(ErrorCode.TOOL_RESULT_BINDING_MISMATCH)
        return run

    async def _verify_entry_attribution(
        self, tenant_id: str, run: RunRecord, user_id: uuid.UUID
    ) -> None:
        conversation = await self._session.scalar(
            sa.select(Conversation).where(
                Conversation.id == run.conversation_id,
                Conversation.tenant_id == tenant_id,
                Conversation.user_id == user_id,
                Conversation.is_deleted.is_(False),
            )
        )
        if conversation is None:
            raise AppError(ErrorCode.TOOL_RESULT_BINDING_MISMATCH)
        if conversation.channel_account_id is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)

    async def _verified_artifacts(
        self, tenant_id: str, registration: WebchatTaskResultRegistration
    ) -> list[uuid.UUID]:
        """每个附件必须是该 Task 的真实产物（`artifact.task_id` 是唯一来源事实）。"""
        if not registration.artifact_ids:
            return []
        rows = await self._session.scalars(
            sa.select(Artifact.id).where(
                Artifact.tenant_id == tenant_id,
                Artifact.id.in_(registration.artifact_ids),
                Artifact.task_id == registration.task_id,
                Artifact.is_deleted.is_(False),
            )
        )
        found = {artifact_id for artifact_id in rows}
        if found != set(registration.artifact_ids):
            raise AppError(ErrorCode.TOOL_RESULT_BINDING_MISMATCH)
        return list(registration.artifact_ids)
