"""入口会话的创建、核属与标题（design §3.3 数据设计 / §3.4 接口设计 / §3.6 综合复核补充契约）。

职责与边界：

- **入口身份**：`RunRequest.channel.bot_id` 是入口键。UUID 形状的入口键就是入口账号的逻辑
  UUID（`conversation.channel_account_id` / `channel_conversation.channel_account_id`）；非
  UUID 形状的入口键**不编造 UUID**——这类入口暂按既有 (tenant,user,agent) 口径解析，且只认
  **未归属**会话（TASK-049 遗留：把入口账号 UUID 带进 RunRequest 由后续装配任务收口）。
- **显式会话核属**：tenant/user/agent 必配；入口可归属时同时核入口归属。跨租户、越权、
  借其他入口（或不可归属入口借用已归属）会话一律 `COMMON_NOT_FOUND`，不泄露存在性。
- **入口指针**：无显式会话的入口按 (tenant,user,agent,入口,外线线程) 五元组在锁内切换当前
  指针；指针缺失时按同一五元组取最近会话（升级连续性），无候选才新建（与消息同事务）。
- **标题**：复用 `conversation.title`——首条真实用户文本归一化后截取 80 个字符，纯附件用
  本地化占位；空会话由读取层显示「新会话」；**不为标题调用模型**。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import RunRequest
from muad_contracts.platform_settings import parse_platform_settings
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime import CanonicalEvent, ChannelConversation, Conversation

#: 标题上限：首条真实用户文本归一化后截取前 80 个字符（design §3.3）。
TITLE_MAX_CHARS = 80
#: 空会话的显示占位（读取层投影；不落库、不为标题调用模型）。
NEW_CONVERSATION_TITLE = "新会话"
#: 纯附件首条消息的本地化占位（按租户平台设置的 `locale.default_locale` 取）。
ATTACHMENT_TITLE_PLACEHOLDER = {"zh-CN": "[附件]", "en-US": "[Attachment]"}
DEFAULT_TITLE_LOCALE = "zh-CN"
#: 「当前会话」排序依据的**用户发言**事件类型（协议标识，不是配置）。
USER_MESSAGE_EVENT = "USER_MESSAGE"
#: 会话状态取值（协议标识，与 Runtime 既有 `conversation.status` 同词表）。
CONVERSATION_ACTIVE = "ACTIVE"

_WHITESPACE_RUN = re.compile(r"\s+")
#: 指针并发登记时的唯一索引列（与 0025 的 partial unique 同形）。
_POINTER_KEY_COLUMNS = (
    "tenant_id",
    "user_id",
    "agent_id",
    "channel_account_id",
    "external_thread_id",
)


@dataclass(frozen=True, slots=True)
class ConversationScope:
    """入口会话的核属维度：租户/用户/Agent/入口账号（可归属时）/外线线程。"""

    tenant_id: str
    user_id: uuid.UUID
    agent_id: uuid.UUID
    entry_id: uuid.UUID | None
    external_thread_id: str


def entry_id_of(bot_id: str) -> uuid.UUID | None:
    """入口键 → 入口账号 UUID；**非 UUID 形状返回 None**（不编造入口身份）。"""
    try:
        return uuid.UUID(bot_id)
    except ValueError:
        return None


def external_thread_id_of(
    external_conversation_id: str | None, external_user_id: str | None
) -> str:
    """外线线程：优先外部会话（如 chatid），1:1 回落外部用户（与 0025 回填同一口径）。"""
    return external_conversation_id or external_user_id or ""


def scope_of(request: RunRequest, tenant_id: str) -> ConversationScope:
    """受理请求 → 会话核属维度（入口键/外线线程在这里一次性解析）。"""
    return ConversationScope(
        tenant_id=tenant_id,
        user_id=request.platform_user_id,
        agent_id=request.agent_id,
        entry_id=entry_id_of(request.channel.bot_id),
        external_thread_id=external_thread_id_of(
            request.channel.external_conversation_id, request.channel.external_user_id
        ),
    )


def normalize_title_text(text: str) -> str:
    """标题文本归一化：空白折叠为单空格并去首尾（不改变其余字符）。"""
    return _WHITESPACE_RUN.sub(" ", text).strip()


def attachment_title_placeholder(locale: str) -> str:
    """纯附件标题的本地化占位；未知 locale 回落 schema 默认（不编造语言）。"""
    return ATTACHMENT_TITLE_PLACEHOLDER.get(
        locale, ATTACHMENT_TITLE_PLACEHOLDER[DEFAULT_TITLE_LOCALE]
    )


def derive_title(text: str, *, has_attachments: bool, locale: str) -> str | None:
    """首条消息 → 标题：真实文本归一化后截取 80 字符；纯附件用本地化占位；否则无标题。"""
    normalized = normalize_title_text(text)
    if normalized:
        return normalized[:TITLE_MAX_CHARS]
    if has_attachments:
        return attachment_title_placeholder(locale)
    return None


def display_title(title: str | None) -> str:
    """空会话的读取层显示占位（不落库、不改写行）。"""
    return title if title else NEW_CONVERSATION_TITLE


def title_locale_of(settings: Mapping[str, Any] | None) -> str:
    """标题占位 locale：与执行默认同一份平台设置快照；坏文档在此显式失败，不静默回退。"""
    return parse_platform_settings(settings).locale.default_locale


def conversation_recency_key() -> ColumnElement[datetime]:
    """「当前会话」排序键：**用户最近发言**时间；从未发言回落 `create_time`。

    **不用 `conversation.update_time`**：`EventWriter._next_seq` 对任何事件（含后台收尾写向
    旧会话的 `MEMORY_CONSOLIDATED`）都会 bump 它，按它取最新会把 `/new` 刚建出的新会话顶掉，
    下一条入站（不带会话指针）落回旧会话。用户发言以 `USER_MESSAGE` 事件为准；新会话在用户
    下次发言前以 `create_time` 保持当前。
    """
    last_user_message_at = (
        sa.select(sa.func.max(CanonicalEvent.create_time))
        .where(
            CanonicalEvent.conversation_id == Conversation.id,
            CanonicalEvent.event_type == USER_MESSAGE_EVENT,
            CanonicalEvent.is_deleted.is_(False),
        )
        .correlate(Conversation)
        .scalar_subquery()
    )
    return sa.func.coalesce(last_user_message_at, Conversation.create_time)


def _scope_conditions(scope: ConversationScope) -> list[Any]:
    return [
        Conversation.tenant_id == scope.tenant_id,
        Conversation.user_id == scope.user_id,
        Conversation.agent_id == scope.agent_id,
        Conversation.is_deleted.is_(False),
    ]


def assert_entry_attribution(conversation: Conversation, scope: ConversationScope) -> None:
    """入口核属（双向严格）：可归属入口必须命中同一入口账号；不可归属入口不得借用已归属会话。"""
    if scope.entry_id is not None:
        if conversation.channel_account_id != scope.entry_id:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
    elif conversation.channel_account_id is not None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)


async def load_explicit_conversation(
    session: AsyncSession, scope: ConversationScope, conversation_id: uuid.UUID
) -> Conversation:
    """显式会话核属：租户/用户/Agent/入口同时成立，否则 `COMMON_NOT_FOUND`（不泄露存在性）。"""
    conversation = await session.scalar(
        sa.select(Conversation).where(
            Conversation.id == conversation_id, *_scope_conditions(scope)
        )
    )
    if conversation is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    assert_entry_attribution(conversation, scope)
    return conversation


def _pointer_conditions(scope: ConversationScope) -> list[Any]:
    return [
        ChannelConversation.tenant_id == scope.tenant_id,
        ChannelConversation.user_id == scope.user_id,
        ChannelConversation.agent_id == scope.agent_id,
        ChannelConversation.channel_account_id == scope.entry_id,
        ChannelConversation.external_thread_id == scope.external_thread_id,
        ChannelConversation.is_deleted.is_(False),
    ]


async def lock_pointer(
    session: AsyncSession, scope: ConversationScope
) -> ChannelConversation | None:
    """按五元组取当前指针行锁（`SELECT ... FOR UPDATE`）。"""
    pointer: ChannelConversation | None = await session.scalar(
        sa.select(ChannelConversation).where(*_pointer_conditions(scope)).with_for_update()
    )
    return pointer


async def register_pointer(
    session: AsyncSession, scope: ConversationScope, conversation_id: uuid.UUID
) -> None:
    """登记/切换当前指针：已有行**原地更新**；缺失时插入（并发落败者回读并锁，不建第二行）。"""
    existing = await lock_pointer(session, scope)
    if existing is not None:
        existing.conversation_id = conversation_id
        existing.update_time = sa.func.now()
        await session.flush()
        return
    inserted = await session.scalar(
        pg_insert(ChannelConversation)
        .values(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            agent_id=scope.agent_id,
            channel_account_id=scope.entry_id,
            external_thread_id=scope.external_thread_id,
            conversation_id=conversation_id,
        )
        .on_conflict_do_nothing(
            index_elements=list(_POINTER_KEY_COLUMNS),
            index_where=sa.text("is_deleted = false"),
        )
        .returning(ChannelConversation.id)
    )
    if inserted is None:
        existing = await lock_pointer(session, scope)
        if existing is None:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        existing.conversation_id = conversation_id
        existing.update_time = sa.func.now()
    await session.flush()


async def create_entry_conversation(
    session: AsyncSession, scope: ConversationScope, *, last_message_at: datetime
) -> Conversation:
    """新建入口会话：归因 (tenant,user,agent,入口,线程)，空标题、`last_seq=0`、无活跃 Run。

    不触碰任何既有会话/ Run（设计 API-08：新建不取消旧会话）。
    """
    conversation = Conversation(
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
        agent_id=scope.agent_id,
        status=CONVERSATION_ACTIVE,
        last_seq=0,
        channel_account_id=scope.entry_id,
        external_thread_id=scope.external_thread_id or None,
        last_message_at=last_message_at,
    )
    session.add(conversation)
    await session.flush()
    return conversation


def record_user_message(
    conversation: Conversation, *, text: str, has_attachments: bool, locale: str, at: datetime
) -> None:
    """真实可见用户消息事务内更新会话：`last_message_at` + **首条**标题（标题不调模型）。"""
    conversation.last_message_at = at
    if conversation.title is None:
        conversation.title = derive_title(text, has_attachments=has_attachments, locale=locale)


async def _latest_entry_thread_conversation(
    session: AsyncSession, scope: ConversationScope
) -> Conversation | None:
    """同一入口/线程的最近会话（指针缺失或指向已不可用会话时的升级/恢复候选）。"""
    conversation: Conversation | None = await session.scalar(
        sa.select(Conversation)
        .where(
            *_scope_conditions(scope),
            Conversation.channel_account_id == scope.entry_id,
            sa.func.coalesce(Conversation.external_thread_id, "") == scope.external_thread_id,
        )
        .order_by(conversation_recency_key().desc(), Conversation.create_time.desc())
        .limit(1)
    )
    return conversation


async def _latest_unattributed_conversation(
    session: AsyncSession, scope: ConversationScope
) -> Conversation | None:
    """不可归属入口的既有口径：只取**未归属**会话的「用户最近发言」。

    已归属其他入口的会话对不可归属入口**不可借用**（不把网页/其他入口的历史串进来）。
    """
    conversation: Conversation | None = await session.scalar(
        sa.select(Conversation)
        .where(*_scope_conditions(scope), Conversation.channel_account_id.is_(None))
        .order_by(conversation_recency_key().desc(), Conversation.create_time.desc())
        .limit(1)
    )
    return conversation


async def _load_pointer_target(
    session: AsyncSession, scope: ConversationScope, conversation_id: uuid.UUID
) -> Conversation | None:
    """指针目标必须是本核属维度下、归属本入口的活跃会话；否则视为不可用（待改指）。"""
    conversation = await session.scalar(
        sa.select(Conversation).where(
            Conversation.id == conversation_id, *_scope_conditions(scope)
        )
    )
    if conversation is None or conversation.channel_account_id != scope.entry_id:
        return None
    return conversation


async def resolve_source_conversation(
    session: AsyncSession,
    scope: ConversationScope,
    *,
    conversation_id: uuid.UUID | None,
) -> Conversation | None:
    """精确控制（停止/取消）的来源会话定位：**只读** —— 不创建会话、不移动指针。

    - 显式会话（网页）：`load_explicit_conversation` 的核属（tenant/user/agent/入口），未命中
      即 `COMMON_NOT_FOUND`（不泄露存在性）；
    - 入口+外线线程（入口指针）：当前指针目标 → 同线程最近会话 → `None`（该来源还没有会话
      ⇒ 它没有活跃 Run，`NO_ACTIVE_RUN` 由调用方裁决）。

    与 `resolve_pointer_conversation` 的关键区别：定位不到**不建新会话** —— 一条停止命令不该
    凭空造出一个会话；不可归属入口（无入口身份）同样返回 `None`，不退回 (tenant,user,agent)
    的最新会话（那正是 RISK-F03 要避免的「取消最新 Run」）。
    """
    if conversation_id is not None:
        return await load_explicit_conversation(session, scope, conversation_id)
    if scope.entry_id is None:
        return None
    pointer = await session.scalar(
        sa.select(ChannelConversation).where(*_pointer_conditions(scope))
    )
    if pointer is not None:
        target = await _load_pointer_target(session, scope, pointer.conversation_id)
        if target is not None:
            return target
    return await _latest_entry_thread_conversation(session, scope)


async def resolve_pointer_conversation(
    session: AsyncSession, scope: ConversationScope
) -> Conversation:
    """无显式会话的入口：按五元组**锁内切换当前指针**并返回其会话。

    指针缺失时按同一五元组取最近会话（升级连续性）；无候选才新建（与消息同事务）。
    入口不可归属（入口键不是 UUID 形状）时不编造入口身份：退回 (tenant,user,agent) 口径，
    且只认未归属会话（TASK-049 遗留：把入口账号 UUID 带进 RunRequest 由后续装配任务收口）。
    """
    if scope.entry_id is None:
        latest = await _latest_unattributed_conversation(session, scope)
        if latest is not None:
            return latest
        return await create_entry_conversation(
            session, scope, last_message_at=await database_now(session)
        )
    pointer = await lock_pointer(session, scope)
    if pointer is not None:
        target = await _load_pointer_target(session, scope, pointer.conversation_id)
        if target is not None:
            return target
    candidate = await _latest_entry_thread_conversation(session, scope)
    if candidate is None:
        candidate = await create_entry_conversation(
            session, scope, last_message_at=await database_now(session)
        )
    await register_pointer(session, scope, candidate.id)
    return candidate
