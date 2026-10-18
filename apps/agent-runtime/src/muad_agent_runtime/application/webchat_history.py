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
- **安全公开投影**（查询侧，TASK-051 会与 SSE 侧合并为唯一实现）：只投影 `USER_MESSAGE` /
  `ASSISTANT_MESSAGE` 两类可见消息与安全附件引用；工具参数/结果、SYSTEM、原始推理、未知事件、
  存储键与 `TRANSCRIPT` 一律不公开；`through_seq` 是**会话持久水位**（含活跃 Run 已落库的
  增量事件），不是"已渲染正文"的证明 —— 活跃 Run 的恢复必须走该 Run 自己的游标/安全快照。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import MAX_PAGE_SIZE, RunStatus
from muad_contracts.webchat import (
    ActiveRunStatus,
    ConversationStatus,
    WebchatActiveRun,
    WebchatAttachment,
    WebchatConversationDetail,
    WebchatConversationItem,
    WebchatConversationPage,
    WebchatHistoryMessage,
    WebchatMessagePage,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.runtime import Artifact, CanonicalEvent, Conversation, RunRecord
from .attachments.reference import attachment_ref
from .channel_conversations import ConversationScope, display_title, load_explicit_conversation
from .execution_status import project_execution_status

#: 可见历史消息的事件类型白名单（canonical 协议标识，不是配置；写入方见
#: `channel_conversations.record_user_message` 与 `run_service._finalize_run`）。
HISTORY_MESSAGE_EVENT_TYPES = ("USER_MESSAGE", "ASSISTANT_MESSAGE")
#: 事件类型 → 公开角色（只此两种可见角色；其余事件默认不公开）。
HISTORY_ROLE_BY_EVENT_TYPE: dict[str, Literal["USER", "ASSISTANT"]] = {
    "USER_MESSAGE": "USER",
    "ASSISTANT_MESSAGE": "ASSISTANT",
}
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


def _conversation_conditions(scope: HistoryScope) -> list[Any]:
    return [
        Conversation.tenant_id == scope.tenant_id,
        Conversation.user_id == scope.user_id,
        Conversation.agent_id == scope.agent_id,
        Conversation.channel_account_id == scope.entry_id,
        Conversation.is_deleted.is_(False),
    ]


def _message_conditions(
    scope: HistoryScope, conversation_id: uuid.UUID, watermark: int
) -> list[Any]:
    return [
        CanonicalEvent.tenant_id == scope.tenant_id,
        CanonicalEvent.conversation_id == conversation_id,
        CanonicalEvent.event_type.in_(HISTORY_MESSAGE_EVENT_TYPES),
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


def _artifact_id_of(entry: object) -> uuid.UUID | None:
    """载荷里的附件引用 id；坏引用不猜测（不冒认别的产物），直接跳过。"""
    if not isinstance(entry, dict):
        return None
    try:
        return uuid.UUID(str(entry.get("artifact_id")))
    except (TypeError, ValueError):
        return None


def _attachment_artifact_ids(rows: Sequence[Any]) -> list[uuid.UUID]:
    """可见用户消息载荷里的附件 id（去重保序）—— 附件元信息按页批量取，不逐条回查。"""
    ids: list[uuid.UUID] = []
    for row in rows:
        if HISTORY_ROLE_BY_EVENT_TYPE.get(row.event_type) != "USER":
            continue
        payload = row.payload_json if isinstance(row.payload_json, dict) else {}
        raw = payload.get("attachments")
        if not isinstance(raw, list):
            continue
        for entry in raw:
            artifact_id = _artifact_id_of(entry)
            if artifact_id is not None and artifact_id not in ids:
                ids.append(artifact_id)
    return ids


def _project_attachments(
    raw: object, artifacts: Mapping[uuid.UUID, Artifact]
) -> list[WebchatAttachment]:
    """附件安全投影：只出引用与元信息；失效附件明确 `available=false`，不伪造不可用字节。

    无权威行或尺寸不可投影的引用**不伪造**（不猜 checksum）——直接不出现在公开投影里。
    字节是否可取（清理/预览授权）属下载端点（TASK-055），这里只投影登记事实。
    """
    if not isinstance(raw, list):
        return []
    projected: list[WebchatAttachment] = []
    for entry in raw:
        artifact_id = _artifact_id_of(entry)
        if artifact_id is None:
            continue
        artifact = artifacts.get(artifact_id)
        if artifact is None or artifact.size < 1:
            continue
        ref = attachment_ref(artifact)
        projected.append(
            WebchatAttachment(
                artifact_id=artifact_id,
                kind=ref.kind,
                media_type=ref.media_type,
                size=ref.size,
                filename=ref.filename,
                checksum=ref.checksum,
                available=not artifact.is_deleted,
            )
        )
    return projected


def _history_message(
    row: Any, artifacts: Mapping[uuid.UUID, Artifact]
) -> WebchatHistoryMessage:
    """canonical 行 → 公开消息：白名单字段 + 空正文兜底（坏载荷不泄露、不整页失败）。"""
    payload = row.payload_json if isinstance(row.payload_json, dict) else {}
    text = payload.get("text")
    role = HISTORY_ROLE_BY_EVENT_TYPE.get(row.event_type)
    if role is None:
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    attachments = (
        _project_attachments(payload.get("attachments"), artifacts) if role == "USER" else []
    )
    return WebchatHistoryMessage(
        event_id=row.id,
        seq=row.seq,
        run_id=row.run_id,
        role=role,
        text=text if isinstance(text, str) else "",
        attachments=attachments,
    )


class WebchatHistoryService:
    """历史读取服务：真实 PG 查询 + 安全公开投影（读取路径不做任何写入）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_conversations(
        self, scope: HistoryScope, *, page: int, page_size: int
    ) -> WebchatConversationPage:
        """API-14：入口会话列表（仅本主体/入口/Agent，按 last_message_at/id 倒序）。"""
        _validate_page(page, page_size)
        rows, total = await self._conversation_page(scope, page=page, page_size=page_size)
        active = await self._active_run_statuses(scope, [row.id for row in rows])
        items = [_conversation_item(row, active.get(row.id)) for row in rows]
        return WebchatConversationPage(items=items, page=page, page_size=page_size, total=total)

    async def get_conversation(
        self, scope: HistoryScope, conversation_id: uuid.UUID
    ) -> WebchatConversationDetail:
        """API-15：核属后的会话详情（活跃 Run 供打开历史后的恢复，不暴露内部快照）。

        `active_run.confirmation` 的完整候选与选项文案由 TASK-056 的 resume 语义投影
        （记忆确认的选项在本任务没有已定型的线上词表）——这里不合成选项文案，不编造。
        """
        conversation = await load_explicit_conversation(
            self._session, _conversation_scope(scope), conversation_id
        )
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

    async def list_messages(
        self,
        scope: HistoryScope,
        conversation_id: uuid.UUID,
        *,
        page: int,
        page_size: int,
        through_seq: int | None,
    ) -> WebchatMessagePage:
        """API-09：固定水位分页（首屏冻结、后页沿用；越界游标显式失败）。"""
        _validate_page(page, page_size)
        if page > 1 and through_seq is None:
            # 契约 `WebchatHistoryQuery` 同源：后页必须沿用首屏冻结的水位。
            raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
        conversation = await load_explicit_conversation(
            self._session, _conversation_scope(scope), conversation_id
        )
        watermark = conversation.last_seq if through_seq is None else through_seq
        if watermark > conversation.last_seq:
            raise AppError(ErrorCode.CURSOR_AHEAD)
        rows, total = await self._message_page(
            scope, conversation.id, watermark, page=page, page_size=page_size
        )
        artifacts = await self._artifacts_for(scope, rows)
        items = [_history_message(row, artifacts) for row in reversed(rows)]
        return WebchatMessagePage(
            items=items, page=page, page_size=page_size, total=total, through_seq=watermark
        )

    async def _conversation_page(
        self, scope: HistoryScope, *, page: int, page_size: int
    ) -> tuple[list[Any], int]:
        conditions = _conversation_conditions(scope)
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
                CanonicalEvent.seq,
                CanonicalEvent.run_id,
                CanonicalEvent.event_type,
                CanonicalEvent.payload_json,
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
        artifact_ids = _attachment_artifact_ids(rows)
        if not artifact_ids:
            return {}
        artifacts = await self._session.scalars(
            sa.select(Artifact).where(
                Artifact.tenant_id == scope.tenant_id, Artifact.id.in_(artifact_ids)
            )
        )
        return {artifact.id: artifact for artifact in artifacts}
