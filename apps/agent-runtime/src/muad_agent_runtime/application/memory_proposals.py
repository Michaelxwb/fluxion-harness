"""Trusted proposal sources, complete stored key state and direct user prompts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from muad_agent_core.memory.evidence import verify_quote
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.canonical import canonical_json
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
from .memory_identity import memory_key as build_memory_key

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


def _proposal(
    candidate: RememberMemoryInput,
    run: RunRecord,
    event: CanonicalEvent,
    epoch: int,
    now: datetime,
    expected_version: int | None,
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
        expected_version=expected_version,
        forget_epoch=epoch,
        evidence=MemorySourceEvidence(
            source_event_id=event.id,
            source_seq=event.seq,
            source_scope_revision=revision,
            source_quote=candidate.source_quote,
        ),
        expires_at=expires,
    )


async def _existing_target(
    session: AsyncSession, run: RunRecord, candidate: RememberMemoryInput
) -> tuple[int | None, UserMemory | None]:
    """Version and row a confirmation would overwrite; a new NOTE has no existing target."""

    if candidate.memory_kind == "NOTE":
        return None, None
    key = build_memory_key(candidate.normalized(taxonomy_version=1))
    row = await session.scalar(
        select(UserMemory)
        .where(
            UserMemory.tenant_id == run.tenant_id,
            UserMemory.user_id == run.user_id,
            UserMemory.memory_key == key,
        )
        .with_for_update()
    )
    return (row.version if row is not None else None), row


async def _scope_epoch(session: AsyncSession, run: RunRecord) -> int:
    scope = await session.scalar(
        select(MemoryScope)
        .where(MemoryScope.tenant_id == run.tenant_id, MemoryScope.user_id == run.user_id)
        .with_for_update()
    )
    return scope.forget_epoch if scope is not None else 0


def _prompt(
    proposal_id: UUID,
    proposal: MemoryConfirmationProposal,
    now: datetime,
    *,
    target: UserMemory | None = None,
    timezone: str = "UTC",
    for_text_channel: bool = False,
) -> str:
    candidate = proposal.candidate
    fields = [
        f"待保存记忆，候选编号：{proposal_id}",
        f"类型：{candidate.memory_kind}",
        f"谓词：{candidate.predicate}",
        f"标题：{candidate.title}",
        f"内容：{canonical_json(candidate.content)}",
    ]
    if candidate.summary:
        fields.append(f"摘要：{candidate.summary}")
    if candidate.tags:
        fields.append("标签：" + "、".join(candidate.tags))
    if candidate.scope_state.value == "EXPLICIT" and candidate.scope:
        fields.append("范围：" + canonical_json(candidate.scope))
    if target is not None:
        # 覆盖既有槽位前先把当前状态告诉用户，确认才生效。
        if target.is_deleted:
            fields.append("当前状态：已删除；确认会重新记住")
        elif not target.enabled:
            fields.append("当前状态：已禁用；确认会重新启用")
        if target.expires_at is not None and target.expires_at <= now:
            fields.append("当前状态：已过期；确认将重新设置有效期")
    if candidate.source_quote is not None:
        fields.append(f"来源原话：{candidate.source_quote}")
    if for_text_channel:
        body = "\n".join(f"- {field}" for field in fields[1:])
        return f"{fields[0]}\n\n{body}\n\n请回复“{CONFIRM_VERB}”或“{REJECT_VERB}”。"
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
