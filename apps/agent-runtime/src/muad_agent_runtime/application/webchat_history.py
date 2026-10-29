"""Webchat 历史读取：列表、详情与固定水位分页（design §3.4 API-09/14/15、§3.5 历史读取；B-250/B-04）。

职责与边界：

- **列表**：按 (tenant, user, agent, 入口) 索引查询，`last_message_at DESC NULLS LAST, id DESC`；
  list/count **同一语句同一快照**（同一 WHERE 的 total 子查询与分页子查询 LEFT JOIN），
  活跃 Run 状态按页**一次批量**关联（无逐会话 N+1）。
- **详情**：复用 TASK-049 的 `load_explicit_conversation` 核属（tenant/user/agent/入口 同时成立，
  一律 `COMMON_NOT_FOUND` 不泄露存在性）；返回活跃 Run 的恢复身份（id/status/execution_status）
  与持久水位，不暴露快照、输入原文或内部事件。
- **消息**：**固定水位分页** —— 首屏冻结 `through_seq=conversation.last_seq`，后页沿用同一水位；
  分页期间新消息不移动已固定水位；`through_seq` 超过会话持久水位显式 `CURSOR_AHEAD`；
  page 1 是最新窗口、页内正序、向前加载旧页；总量不被 200 条截断。
- **安全公开投影**（与 SSE 侧**唯一实现**，见 `application/public_events.py`）：只投影
  `USER_MESSAGE` / `ASSISTANT_MESSAGE` 两类可见消息与安全附件引用；工具参数/结果、SYSTEM、
  原始推理、未知事件、存储键与 `TRANSCRIPT` 一律不公开；`through_seq` 是**会话持久水位**
  （含活跃 Run 已落库的增量事件），不是"已渲染正文"的证明 —— 活跃 Run 的恢复必须走该 Run
  自己的游标/安全快照。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import MAX_PAGE_SIZE, RunStatus
from muad_contracts.webchat import (
    ActiveRunStatus,
    ConversationStatus,
    WebchatActiveRun,
    WebchatConfirmation,
    WebchatConfirmationOption,
    WebchatConversationDetail,
    WebchatConversationItem,
    WebchatConversationPage,
    WebchatConversationScope,
    WebchatHistoryMessage,
    WebchatMessagePage,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime import (
    Artifact,
    CanonicalEvent,
    Conversation,
    RunInterrupt,
    RunRecord,
)
from .channel_conversations import ConversationScope, display_title, load_explicit_conversation
from .conversation_policy import ConversationPolicy, assert_conversation_live
from .execution_status import project_execution_status
from .memory_confirmation_resume import (
    CONFIRM_VERB,
    REJECT_VERB,
    confirmation_selection,
    validated_proposal,
)
from .public_events import PUBLIC_MESSAGE_EVENT_TYPES, project_public_fact, visible_attachment_ids

#: 活跃 Run 状态（与 `uq_run_record_active_conversation` partial unique 谓词同源）。
ACTIVE_RUN_STATUSES: tuple[RunStatus, ...] = (
    RunStatus.CREATED,
    RunStatus.RUNNING,
    RunStatus.WAITING_TOOL,
    RunStatus.WAITING_INPUT,
)
#: 活跃 Run 状态 → 契约字面量（字面量与查询谓词同源，避免两处漂移）。
ACTIVE_RUN_STATUS_BY_RUN: dict[RunStatus, ActiveRunStatus] = {
    RunStatus.CREATED: "CREATED",
    RunStatus.RUNNING: "RUNNING",
    RunStatus.WAITING_TOOL: "WAITING_TOOL",
    RunStatus.WAITING_INPUT: "WAITING_INPUT",
}
#: 会话状态取值（`conversation.status` 既有词表，仅允许两值）。
CONVERSATION_STATUSES: tuple[ConversationStatus, ...] = ("ACTIVE", "ARCHIVED")


@dataclass(frozen=True, slots=True)
class HistoryScope:
    """历史读取的核属维度：入口与 Agent 都显式（详情/消息由受信调用方传入后复核）。"""

    tenant_id: str
    user_id: uuid.UUID
    agent_id: uuid.UUID
    entry_id: uuid.UUID


def _conversation_scope(scope: HistoryScope) -> ConversationScope:
    """复用 TASK-049 的核属维度（读取与外线线程无关，不重写归属校验）。"""
    return ConversationScope(
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
        agent_id=scope.agent_id,
        entry_id=scope.entry_id,
        external_thread_id="",
    )


def _validate_page(page: int, page_size: int) -> None:
    if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)


def _conversation_conditions(
    scope: HistoryScope, *, cutoff: datetime | None = None
) -> list[Any]:
    conditions: list[Any] = [
        Conversation.tenant_id == scope.tenant_id,
        Conversation.user_id == scope.user_id,
        Conversation.agent_id == scope.agent_id,
        Conversation.channel_account_id == scope.entry_id,
        Conversation.is_deleted.is_(False),
    ]
    if cutoff is not None:
        # 保留期以最近一次真实可见活动为准；NULL 回落创建时间（与本列表的排序口径一致）。
        conditions.append(
            sa.func.coalesce(Conversation.last_message_at, Conversation.create_time) >= cutoff
        )
    return conditions


async def _retention_cutoff(
    session: AsyncSession, policy: ConversationPolicy | None
) -> datetime | None:
    """读取边界只取一次 PG 时钟；0 天是显式「永不过期」，不额外查时钟。"""
    if policy is None or policy.conversation_retention_days == 0:
        return None
    return policy.retention_cutoff(await database_now(session))


async def _assert_conversation_live(
    session: AsyncSession,
    conversation: Conversation,
    policy: ConversationPolicy | None,
) -> None:
    """详情/消息/续聊共用的保留期裁决；超期一律稳定码 `CONVERSATION_EXPIRED`。"""
    if policy is None or policy.conversation_retention_days == 0:
        return
    assert_conversation_live(
        policy,
        last_message_at=conversation.last_message_at,
        create_time=conversation.create_time,
        now=await database_now(session),
    )


def _message_conditions(
    scope: HistoryScope, conversation_id: uuid.UUID, watermark: int
) -> list[Any]:
    return [
        CanonicalEvent.tenant_id == scope.tenant_id,
        CanonicalEvent.conversation_id == conversation_id,
        CanonicalEvent.event_type.in_(PUBLIC_MESSAGE_EVENT_TYPES),
        # 无 Run 归属的历史事件无法证实来源，不冒认成网页历史（与「无法证实归属保留审计」同口径）。
        CanonicalEvent.run_id.is_not(None),
        CanonicalEvent.seq <= watermark,
        CanonicalEvent.is_deleted.is_(False),
    ]


async def _paged_rows(
    session: AsyncSession, *, page_stmt: sa.Select[Any], total_stmt: sa.Select[Any]
) -> tuple[list[Any], int]:
    """同谓词**同快照**：total 子查询（恒 1 行）与分页子查询在一条语句里 LEFT JOIN。

    分页越界（page 子查询为空）时 total 仍然正确 —— 这正是"list/count 同条件"的可验证含义。
    """
    page_sub = page_stmt.subquery("page_rows")
    total_sub = total_stmt.subquery("total_row")
    stmt = (
        sa.select(total_sub.c.total, *page_sub.c)
        .select_from(total_sub)
        .outerjoin(page_sub, sa.true())
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return [], 0
    return [row for row in rows if row.id is not None], int(rows[0].total)


def _active_run_status(status: RunStatus) -> ActiveRunStatus:
    """RunStatus → 活跃状态字面量；非活跃状态是数据异常，显式失败不静默投影。"""
    mapped = ACTIVE_RUN_STATUS_BY_RUN.get(status)
    if mapped is None:
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return mapped


def _conversation_status(raw: str) -> ConversationStatus:
    """会话状态字面量；未知值显式失败（不把未知状态当活跃历史暴露）。"""
    if raw not in CONVERSATION_STATUSES:
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return raw


def _conversation_item(row: Any, active_status: ActiveRunStatus | None) -> WebchatConversationItem:
    """列表项投影：标题用读取层占位，最近活动缺失回落 `create_time`（不返回 NULL 时间）。"""
    return WebchatConversationItem(
        conversation_id=row.id,
        title=display_title(row.title),
        create_time=row.create_time,
        last_message_at=row.last_message_at or row.create_time,
        last_run_id=row.last_run_id,
        status=_conversation_status(row.status),
        active_run_status=active_status,
    )


def _visible_message(
    row: Any, artifacts: Mapping[uuid.UUID, Artifact]
) -> WebchatHistoryMessage | None:
    """canonical 行 → 可见消息（共用投影的唯一入口；非消息/不可证实归属返回 None）。"""
    fact = project_public_fact(row, artifacts)
    return fact if isinstance(fact, WebchatHistoryMessage) else None


class WebchatHistoryService:
    """历史读取服务：真实 PG 查询 + 安全公开投影（读取路径不做任何写入）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_conversations(
        self,
        scope: HistoryScope,
        *,
        page: int,
        page_size: int,
        policy: ConversationPolicy | None = None,
    ) -> WebchatConversationPage:
        """API-14：入口会话列表（仅本主体/入口/Agent，按 last_message_at/id 倒序）。"""
        _validate_page(page, page_size)
        cutoff = await _retention_cutoff(self._session, policy)
        rows, total = await self._conversation_page(
            scope, page=page, page_size=page_size, cutoff=cutoff
        )
        active = await self._active_run_statuses(scope, [row.id for row in rows])
        items = [_conversation_item(row, active.get(row.id)) for row in rows]
        return WebchatConversationPage(items=items, page=page, page_size=page_size, total=total)

    async def get_conversation(
        self,
        scope: HistoryScope,
        conversation_id: uuid.UUID,
        *,
        policy: ConversationPolicy | None = None,
    ) -> WebchatConversationDetail:
        """API-15：核属后的会话详情（活跃 Run 供打开历史后的恢复，不暴露内部快照）。

        `active_run.confirmation` 是**待确认恢复**（TASK-056）：完整候选（prompt 含键/类别/
        内容/来源原话/有效期）加 resume 语义的选项值 —— 客户端回传的 `selection` 就是这里的
        `value`，绑定用的 `submission_id`/`source_event_id` 也是这里的字段。
        """
        conversation = await load_explicit_conversation(
            self._session, _conversation_scope(scope), conversation_id
        )
        await _assert_conversation_live(self._session, conversation, policy)
        entry_id = conversation.channel_account_id
        if entry_id is None:
            # 核属已保证入口归属；这里防御式显式失败，不返回不可归属的网页历史。
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return WebchatConversationDetail(
            conversation_id=conversation.id,
            entry_id=entry_id,
            agent_id=conversation.agent_id,
            title=display_title(conversation.title),
            status=_conversation_status(conversation.status),
            last_seq=conversation.last_seq,
            last_run_id=conversation.last_run_id,
            active_run=await self._active_run_view(conversation),
        )

    async def resolve_conversation_scope(
        self, *, tenant_id: str, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> WebchatConversationScope:
        """API-09/15 的入口解析接线（TASK-052）：本主体名下的会话 → (entry_id, agent_id)。

        公共路径（`GET /webchat/conversations/{id}[...]`）不带入口，网关据此解析后再走**入口
        显式**的读取端点。只按 (tenant, user) 核属：不存在/他主体/不可归属一律
        `COMMON_NOT_FOUND`（不泄露存在性），入口与 Agent 都从会话行导出、不由调用方声明。
        """
        conversation = await self._session.scalar(
            sa.select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == tenant_id,
                Conversation.user_id == user_id,
                Conversation.is_deleted.is_(False),
            )
        )
        if conversation is None or conversation.channel_account_id is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return WebchatConversationScope(
            conversation_id=conversation.id,
            entry_id=conversation.channel_account_id,
            agent_id=conversation.agent_id,
        )

    async def list_messages(
        self,
        scope: HistoryScope,
        conversation_id: uuid.UUID,
        *,
        page: int,
        page_size: int,
        through_seq: int | None,
        policy: ConversationPolicy | None = None,
    ) -> WebchatMessagePage:
        """API-09：固定水位分页（首屏冻结、后页沿用；越界游标显式失败）。"""
        _validate_page(page, page_size)
        if page > 1 and through_seq is None:
            # 契约 `WebchatHistoryQuery` 同源：后页必须沿用首屏冻结的水位。
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        conversation = await load_explicit_conversation(
            self._session, _conversation_scope(scope), conversation_id
        )
        await _assert_conversation_live(self._session, conversation, policy)
        watermark = conversation.last_seq if through_seq is None else through_seq
        if watermark > conversation.last_seq:
            raise AppError(ErrorCode.CURSOR_AHEAD)
        rows, total = await self._message_page(
            scope, conversation.id, watermark, page=page, page_size=page_size
        )
        artifacts = await self._artifacts_for(scope, rows)
        items = [
            item
            for row in reversed(rows)
            if (item := _visible_message(row, artifacts)) is not None
        ]
        return WebchatMessagePage(
            items=items, page=page, page_size=page_size, total=total, through_seq=watermark
        )

    async def _conversation_page(
        self,
        scope: HistoryScope,
        *,
        page: int,
        page_size: int,
        cutoff: datetime | None = None,
    ) -> tuple[list[Any], int]:
        conditions = _conversation_conditions(scope, cutoff=cutoff)
        page_stmt = (
            sa.select(
                Conversation.id,
                Conversation.title,
                Conversation.create_time,
                Conversation.last_message_at,
                Conversation.last_run_id,
                Conversation.status,
            )
            .where(*conditions)
            .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.id.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        total_stmt = (
            sa.select(sa.func.count().label("total")).select_from(Conversation).where(*conditions)
        )
        return await _paged_rows(self._session, page_stmt=page_stmt, total_stmt=total_stmt)

    async def _active_run_statuses(
        self, scope: HistoryScope, conversation_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, ActiveRunStatus]:
        """活跃状态批量关联：一页一条查询（同会话至多一个活跃 Run 由 partial unique 保证）。"""
        if not conversation_ids:
            return {}
        rows = await self._session.execute(
            sa.select(RunRecord.conversation_id, RunRecord.status).where(
                RunRecord.tenant_id == scope.tenant_id,
                RunRecord.conversation_id.in_(conversation_ids),
                RunRecord.status.in_(ACTIVE_RUN_STATUSES),
                RunRecord.is_deleted.is_(False),
            )
        )
        return {row.conversation_id: _active_run_status(row.status) for row in rows}

    async def _active_run_view(self, conversation: Conversation) -> WebchatActiveRun | None:
        run = await self._session.scalar(
            sa.select(RunRecord).where(
                RunRecord.tenant_id == conversation.tenant_id,
                RunRecord.conversation_id == conversation.id,
                RunRecord.status.in_(ACTIVE_RUN_STATUSES),
                RunRecord.is_deleted.is_(False),
            )
        )
        if run is None:
            return None
        if conversation.last_run_id != run.id:
            # 活跃 Run 必是最近一轮（`_create_run` 同事务写指针）；不一致是数据异常，显式失败。
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return WebchatActiveRun(
            id=run.id,
            status=_active_run_status(run.status),
            execution_status=project_execution_status(run),
            confirmation=await self._confirmation_view(run),
        )

    async def _confirmation_view(self, run: RunRecord) -> WebchatConfirmation | None:
        """待确认恢复：WAITING_INPUT 的**完整候选**投影（`WebchatActiveRun.confirmation`）。

        只有 `MEMORY_CONFIRMATION` 的 WAITING 中断是可确认的（别的等待态不是记忆确认）；
        候选必须与它展示过的 canonical 证据一致（`validated_proposal` 与消费路径同一份校验）——
        投影一份确认不了、或确认了别的来源的候选，比显式失败更糟。选项值取自 resume 语义
        （`confirmation_selection`），不在这里另拼一份文本。
        """
        if run.status != RunStatus.WAITING_INPUT:
            return None
        interrupt = await self._session.scalar(
            sa.select(RunInterrupt).where(
                RunInterrupt.tenant_id == run.tenant_id,
                RunInterrupt.run_id == run.id,
                RunInterrupt.conversation_id == run.conversation_id,
                RunInterrupt.interrupt_type == "MEMORY_CONFIRMATION",
                RunInterrupt.status == "WAITING",
                RunInterrupt.is_deleted.is_(False),
            )
        )
        if interrupt is None:
            return None
        proposal = await validated_proposal(self._session, run, interrupt)
        if proposal is None or interrupt.expires_at is None:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return WebchatConfirmation(
            submission_id=proposal.submission_id,
            source_event_id=proposal.evidence.source_event_id,
            prompt=interrupt.prompt_text,
            options=[
                WebchatConfirmationOption(
                    value=confirmation_selection("CONFIRM", interrupt.id), label=CONFIRM_VERB
                ),
                WebchatConfirmationOption(
                    value=confirmation_selection("REJECT", interrupt.id), label=REJECT_VERB
                ),
            ],
            expires_at=interrupt.expires_at,
        )

    async def _message_page(
        self,
        scope: HistoryScope,
        conversation_id: uuid.UUID,
        watermark: int,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[Any], int]:
        conditions = _message_conditions(scope, conversation_id, watermark)
        page_stmt = (
            sa.select(
                CanonicalEvent.id,
                CanonicalEvent.conversation_id,
                CanonicalEvent.seq,
                CanonicalEvent.run_id,
                CanonicalEvent.event_type,
                CanonicalEvent.stream_type,
                CanonicalEvent.payload_json,
                CanonicalEvent.artifact_id,
                CanonicalEvent.create_time,
            )
            .where(*conditions)
            .order_by(CanonicalEvent.seq.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        total_stmt = (
            sa.select(sa.func.count().label("total")).select_from(CanonicalEvent).where(*conditions)
        )
        return await _paged_rows(self._session, page_stmt=page_stmt, total_stmt=total_stmt)

    async def _artifacts_for(
        self, scope: HistoryScope, rows: Sequence[Any]
    ) -> dict[uuid.UUID, Artifact]:
        """按页批量取附件权威行（含软删 tombstone：失效附件要投影 `available=false`）。"""
        artifact_ids = visible_attachment_ids(rows)
        if not artifact_ids:
            return {}
        artifacts = await self._session.scalars(
            sa.select(Artifact).where(
                Artifact.tenant_id == scope.tenant_id, Artifact.id.in_(artifact_ids)
            )
        )
        return {artifact.id: artifact for artifact in artifacts}
