"""Trusted proposal sources, complete stored key state and direct user prompts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from muad_agent_core.memory.evidence import verify_quote
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import (
    MemoryConfirmationProposal,
    MemorySourceEvidence,
    RememberMemoryInput,
    memory_hash,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db_clock import database_now
from ..infrastructure.models.async_tools import RunContinuation
from ..infrastructure.models.runtime import (
    CanonicalEvent,
    MemoryScope,
    RunInterrupt,
    RunRecord,
    RunSubmission,
    UserMemory,
)
from .async_tools.continuation_service import ExecutionIdentity, ensure_execution, lock_execution

#: 用户确认/拒绝的动词：文本解析、WebChat 选项值与文本渠道提示共用这一份。
CONFIRM_VERB = "确认"
REJECT_VERB = "拒绝"


async def locked_confirmation_execution(
    session: AsyncSession,
    identity: ExecutionIdentity,
) -> tuple[RunRecord, RunContinuation | None, datetime]:
    run, saved = await lock_execution(session, identity, await database_now(session))
    # Row-lock acquisition may outlive the lease/deadline; fence with the post-lock clock.
    now = await database_now(session)
    ensure_execution(run, identity, now)
    return run, saved, now


async def ensure_no_memory_confirmation(session: AsyncSession, identity: ExecutionIdentity) -> None:
    pending = await session.scalar(
        select(RunInterrupt.id).where(
            RunInterrupt.tenant_id == identity.tenant_id,
            RunInterrupt.run_id == identity.run_id,
            RunInterrupt.interrupt_type == "MEMORY_CONFIRMATION",
            RunInterrupt.status == "WAITING",
            RunInterrupt.is_deleted.is_(False),
        )
    )
    if pending is not None:
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_PENDING)


async def confirmation_source(session: AsyncSession, run: RunRecord, submission_id: UUID) -> CanonicalEvent:
    submission = await session.scalar(
        select(RunSubmission).where(
            RunSubmission.id == submission_id,
            RunSubmission.tenant_id == run.tenant_id,
            RunSubmission.run_id == run.id,
            RunSubmission.conversation_id == run.conversation_id,
            RunSubmission.actor_user_id == run.user_id,
            RunSubmission.is_deleted.is_(False),
        )
    )
    event = await session.scalar(
        select(CanonicalEvent)
        .where(
            CanonicalEvent.tenant_id == run.tenant_id,
            CanonicalEvent.run_id == run.id,
            CanonicalEvent.conversation_id == run.conversation_id,
            CanonicalEvent.submission_id == submission_id,
            CanonicalEvent.event_type == "USER_MESSAGE",
            CanonicalEvent.is_deleted.is_(False),
        )
        .order_by(CanonicalEvent.seq.desc())
        .limit(1)
    )
    if submission is None or event is None or not isinstance(event.payload_json.get("text"), str):
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_INVALID)
    return event


async def _memory_state(session: AsyncSession, run: RunRecord, key: str) -> tuple[UserMemory | None, int]:
    scope = await session.scalar(
        select(MemoryScope)
        .where(
            MemoryScope.tenant_id == run.tenant_id,
            MemoryScope.user_id == run.user_id,
        )
        .with_for_update()
    )
    row = await session.scalar(
        select(UserMemory)
        .where(
            UserMemory.tenant_id == run.tenant_id,
            UserMemory.user_id == run.user_id,
            UserMemory.memory_key == key,
        )
        .with_for_update()
    )
    return row, scope.forget_epoch if scope is not None else 0


def _proposal(
    candidate: RememberMemoryInput,
    run: RunRecord,
    event: CanonicalEvent,
    row: UserMemory | None,
    epoch: int,
    now: datetime,
) -> MemoryConfirmationProposal:
    text = event.payload_json["text"]
    quote = candidate.source_quote
    if quote is not None and (not isinstance(text, str) or not verify_quote(text, quote)):
        candidate = candidate.model_copy(update={"source_quote": None})
    revision = event.payload_json.get("source_scope_revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_INVALID)
    expires = now + timedelta(seconds=300)
    if run.deadline_at is not None:
        expires = min(expires, run.deadline_at)
    if event.submission_id is None:
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_INVALID)
    return MemoryConfirmationProposal(
        tenant_id=run.tenant_id,
        user_id=run.user_id,
        run_id=run.id,
        submission_id=event.submission_id,
        candidate=candidate,
        candidate_hash=memory_hash(candidate.model_dump(mode="json")),
        expected_version=row.version if row is not None else None,
        forget_epoch=epoch,
        evidence=MemorySourceEvidence(
            source_event_id=event.id,
            source_seq=event.seq,
            source_scope_revision=revision,
            source_quote=candidate.source_quote,
        ),
        expires_at=expires,
    )


def _prompt(
    proposal_id: UUID,
    proposal: MemoryConfirmationProposal,
    row: UserMemory | None,
    now: datetime,
    *,
    timezone: str = "UTC",
    for_text_channel: bool = False,
) -> str:
    states: list[str] = []
    if row is not None:
        if row.is_deleted:
            states.append("已删除；确认会重新记住")
        if not row.enabled:
            states.append("已禁用；确认会重新启用")
        if row.expires_at is not None and row.expires_at <= now:
            states.append("已过期；确认会重新设置有效期")
    candidate = proposal.candidate
    category = {
        "PREFERENCE": "偏好",
        "WORK_STYLE": "工作方式",
        "EXPLICIT": "明确事实",
        "PROCEDURE": "过程经验",
        "FAILURE_PATTERN": "失败模式",
        "VERIFICATION": "验证方法",
    }[candidate.category]
    fields = [
        f"待保存记忆，候选编号：{proposal_id}",
        f"键：{candidate.memory_key}",
        f"类别：{category}",
        f"内容：{candidate.value}",
    ]
    if candidate.source_quote is not None:
        fields.append(f"来源原话：{candidate.source_quote}")
    if states:
        fields.append("原条目状态：" + "；".join(states))
    if for_text_channel:
        # 企微等文本渠道：Markdown 列表给每个字段单独一行（单换行会折叠），且不展示有效期
        # 字段——有效期由服务端强制，WebChat 卡片单独展示期限，文本里重复只会制造噪音。
        body = "\n".join(f"- {field}" for field in fields[1:])
        return (
            f"{fields[0]}\n\n{body}\n\n"
            f"请回复“{CONFIRM_VERB}”或“{REJECT_VERB}”。"
        )
    body = fields[0] + "\n\n" + "\n".join(fields[1:])
    return (
        f"{body}\n\n有效至：{_format_expiry(proposal.expires_at, timezone)}\n"
        f"请回复“{CONFIRM_VERB}”或“{REJECT_VERB}”。"
    )


def _format_expiry(expires_at: datetime, timezone: str) -> str:
    """按 Run 冻结的用户时区展示期限；坏时区显式回退 UTC（不让确认消息发送失败）。"""
    try:
        local = expires_at.astimezone(ZoneInfo(timezone))
    except (ZoneInfoNotFoundError, ValueError):
        local = expires_at.astimezone(UTC)
    return local.strftime("%Y-%m-%d %H:%M:%S %z")
