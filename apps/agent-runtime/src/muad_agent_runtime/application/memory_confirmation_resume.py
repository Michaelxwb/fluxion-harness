"""Consume a real user confirmation with the caller's canonical/Run transaction."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import (
    FactContent,
    MemoryCandidate,
    MemoryCategory,
    MemoryConfirmationInput,
    MemoryConfirmationProposal,
    memory_hash,
)
from muad_contracts.platform_settings import MemoryPolicySettings
from muad_contracts.tasks import ContractModel
from pydantic import AwareDatetime, StrictBool, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime import CanonicalEvent, RunInterrupt, RunRecord, UserMemory
from .memory_commands import MemoryCommand, MemoryMutation, MemoryWriteContext
from .memory_observability import after_commit, observe
from .memory_proposals import confirmation_source
from .memory_service import MemoryService
from .run_events import EventWriter

logger = logging.getLogger(__name__)
#: 用户确认/拒绝的动词：文本解析（`_ANSWER`）与 API-15 的选项值（`confirmation_selection`）
#: **共用这一份**——投影出来的选项就是 resume 能消费的文本，两处各拼一份就会漂移。
CONFIRM_VERB = "确认"
REJECT_VERB = "拒绝"
_ANSWER = re.compile(
    rf"({CONFIRM_VERB}|{REJECT_VERB})\s+"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.I,
)


@dataclass(frozen=True, slots=True)
class ConfirmationBinding:
    """网页 resume 携带的原提议身份（API-08 `submission_id` + `source_event_id` 两个判别键）。

    两个都要：半个绑定不是请求语义（`ResumeRunRequest` 在校验层拒绝），应用层因此不用处理
    「只有一半」的中间态。
    """

    submission_id: UUID
    source_event_id: UUID


@dataclass(frozen=True, slots=True)
class PendingConfirmation:
    interrupt: RunInterrupt
    answer: MemoryConfirmationInput


def confirmation_selection(action: Literal["CONFIRM", "REJECT"], proposal_id: UUID) -> str:
    """选项值 = resume 能消费的文本：动词与编号格式与 `_ANSWER` 同源（不另拼一份）。"""
    return f"{CONFIRM_VERB if action == 'CONFIRM' else REJECT_VERB} {proposal_id}"


class ConfirmationResult(ContractModel):
    outcome: Literal["CONFIRMED", "REJECTED", "STALE"]
    saved: StrictBool = False
    memory_key: str | None = None
    version: int | None = None
    expires_at: AwareDatetime | None = None


def prepare_confirmation(
    interrupts: list[RunInterrupt],
    text: str,
    *,
    confirmation: ConfirmationBinding | None = None,
) -> PendingConfirmation | None:
    """`interrupts` 是该 Run 的全部中断：终态提议的确认必须被拒，不得静默降级为普通消息。

    `confirmation` 是网页 resume 的原提议身份（API-08）：给出时必须与**锁内读到的**原提议逐项
    一致 —— 别的 submission/source 的确认不能消费本 Run 的待确认（RISK-F03），不一致即显式
    `MEMORY_CONFIRMATION_INVALID` 且不消费 source。
    """
    pending = [
        row
        for row in interrupts
        if row.interrupt_type == "MEMORY_CONFIRMATION" and row.status == "WAITING"
    ]
    match = _ANSWER.fullmatch(text.strip())
    if not pending:
        reference = UUID(match.group(2)) if match is not None else None
        revoked = [
            row
            for row in interrupts
            if row.interrupt_type == "MEMORY_CONFIRMATION" and row.id == reference
        ]
        if revoked:
            observe("memory_confirmation_total", labels={"outcome": "EXPIRED"})
            raise AppError(ErrorCode.MEMORY_CONFIRMATION_EXPIRED)
        return None
    if len(pending) != 1 or match is None or UUID(match.group(2)) != pending[0].id:
        observe("memory_confirmation_total", labels={"outcome": "INVALID"})
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_INVALID)
    _assert_binding(pending[0], confirmation)
    return PendingConfirmation(
        pending[0],
        MemoryConfirmationInput(
            proposal_id=pending[0].id,
            action="CONFIRM" if match.group(1) == CONFIRM_VERB else "REJECT",
        ),
    )


def _assert_binding(interrupt: RunInterrupt, confirmation: ConfirmationBinding | None) -> None:
    """原提议身份逐项复核（锁内、消费之前）：坏提议或不一致一律 INVALID，不消费 source。"""
    if confirmation is None:
        return
    try:
        proposal = MemoryConfirmationProposal.model_validate(interrupt.proposal_json)
    except ValidationError as exc:
        observe("memory_confirmation_total", labels={"outcome": "INVALID"})
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_INVALID) from exc
    if (
        proposal.submission_id != confirmation.submission_id
        or proposal.evidence.source_event_id != confirmation.source_event_id
    ):
        observe("memory_confirmation_total", labels={"outcome": "INVALID"})
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_INVALID)


async def consume_confirmation(
    session: AsyncSession,
    run: RunRecord,
    pending: PendingConfirmation,
    *,
    submission_id: UUID,
    settings: MemoryPolicySettings,
    credential_canaries: tuple[str, ...],
) -> None:
    tenant_id, run_id = run.tenant_id, run.id
    try:
        await _consume_confirmation(session, run, pending, submission_id=submission_id,
            settings=settings, credential_canaries=credential_canaries)
    except (SQLAlchemyError, AppError) as exc:
        outcome = ("EXPIRED" if isinstance(exc, AppError)
            and exc.code == ErrorCode.MEMORY_CONFIRMATION_EXPIRED else "ERROR")
        observe("memory_confirmation_total", labels={"outcome": outcome})
        logger.warning("memory_confirmation_failed", extra={"tenant_id": tenant_id,
            "run_id": str(run_id), "error_type": type(exc).__name__})
        raise


async def _consume_confirmation(
    session: AsyncSession,
    run: RunRecord,
    pending: PendingConfirmation,
    *,
    submission_id: UUID,
    settings: MemoryPolicySettings,
    credential_canaries: tuple[str, ...],
) -> None:
    interrupt = pending.interrupt
    if pending.answer.action == "REJECT":
        result = ConfirmationResult(outcome="REJECTED")
    else:
        if not settings.write_enabled:
            raise AppError(ErrorCode.FORBIDDEN)
        proposal = await validated_proposal(session, run, interrupt)
        result = (
            ConfirmationResult(outcome="STALE")
            if proposal is None
            else await _confirm(
                session, run, proposal, interrupt, submission_id, settings, credential_canaries
            )
        )
    now = await database_now(session)
    _ensure_deadline(run, now)
    payload = result.model_dump(mode="json")
    text = _result_text(result)
    await EventWriter(session).append(
        tenant_id=run.tenant_id,
        conversation_id=run.conversation_id,
        run_id=run.id,
        submission_id=submission_id,
        event_type="MEMORY_CONFIRMATION_RESOLVED",
        source_event_id=interrupt.id,
        stream_type="message.delta",
        payload={**payload, "proposal_id": str(interrupt.id), "text": text, "delta": text},
    )
    interrupt.status, interrupt.resolved_at, interrupt.update_time = "RESOLVED", now, now
    interrupt.resolution_json = {**payload, "submission_id": str(submission_id)}
    await session.flush()
    after_commit(session, "memory_confirmation_total", labels={"outcome": result.outcome})


async def validated_proposal(
    session: AsyncSession,
    run: RunRecord,
    interrupt: RunInterrupt,
) -> MemoryConfirmationProposal | None:
    """提议必须与它**展示过的** canonical 证据逐项一致；不一致返回 None（调用方显式裁决）。

    消费路径把它判为 `STALE`（提议已失效，不保存）；读取路径（API-15 待确认恢复）把它判为
    数据异常 —— 同一份校验，两个消费者不会各自解释一遍。
    """
    try:
        proposal = MemoryConfirmationProposal.model_validate(interrupt.proposal_json)
        if (proposal.tenant_id, proposal.user_id, proposal.run_id) != (run.tenant_id, run.user_id, run.id):
            raise ValueError("proposal ownership mismatch")
        source = await confirmation_source(session, run, proposal.submission_id)
        shown = await session.scalar(
            select(CanonicalEvent).where(
                CanonicalEvent.tenant_id == run.tenant_id,
                CanonicalEvent.run_id == run.id,
                CanonicalEvent.conversation_id == run.conversation_id,
                CanonicalEvent.is_deleted.is_(False),
                CanonicalEvent.event_type == "MEMORY_CONFIRMATION_REQUIRED",
                CanonicalEvent.payload_json["proposal_id"].astext == str(interrupt.id),
            )
        )
        if (
            source.id != proposal.evidence.source_event_id
            or source.seq != proposal.evidence.source_seq
            or source.payload_json.get("source_scope_revision") != proposal.evidence.source_scope_revision
            or shown is None
            or shown.submission_id != proposal.submission_id
            or shown.payload_json.get("prompt") != interrupt.prompt_text
            or shown.payload_json.get("proposal_hash") != memory_hash(proposal.model_dump(mode="json"))
            or interrupt.expires_at != proposal.expires_at
        ):
            raise ValueError("proposal does not match its displayed canonical evidence")
        return proposal
    except (ValueError, AppError) as exc:
        logger.warning(
            "memory_confirmation_proposal_invalid",
            extra={
                "tenant_id": run.tenant_id,
                "run_id": str(run.id),
                "error_type": type(exc).__name__,
            },
        )
        return None


async def _confirm(
    session: AsyncSession,
    run: RunRecord,
    proposal: MemoryConfirmationProposal,
    interrupt: RunInterrupt,
    submission_id: UUID,
    settings: MemoryPolicySettings,
    credential_canaries: tuple[str, ...],
) -> ConfirmationResult:
    source = await confirmation_source(session, run, submission_id)
    ctx = MemoryWriteContext(
        tenant_id=run.tenant_id,
        user_id=run.user_id,
        actor_type="USER",
        actor_id=str(run.user_id),
        settings=settings,
        credential_canaries=credential_canaries,
        source_run_id=run.id,
        conversation_id=run.conversation_id,
        evidence=proposal.evidence,
        confirmation_event_id=source.id,
        expected_forget_epoch=proposal.forget_epoch,
    )
    scope = await MemoryService.lock_scope(session, ctx)
    now = await database_now(session)
    _ensure_deadline(run, now)
    row = await session.scalar(
        select(UserMemory)
        .where(
            UserMemory.tenant_id == run.tenant_id,
            UserMemory.user_id == run.user_id,
            UserMemory.memory_key == proposal.candidate.memory_key,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        proposal.expires_at <= now
        or scope.forget_epoch != proposal.forget_epoch
        or (row.version if row is not None else None) != proposal.expected_version
    ):
        return ConfirmationResult(outcome="STALE")
    command = _confirmation_command(proposal, row, interrupt.id, source.id)
    outcome = await MemoryService().execute_in_session(session, ctx, command)
    if outcome.response.status == "STALE":
        return ConfirmationResult(outcome="STALE")
    return await _saved_result(session, run, proposal.candidate.memory_key)


def _confirmation_command(
    proposal: MemoryConfirmationProposal,
    row: UserMemory | None,
    interrupt_id: UUID,
    source_id: UUID,
) -> MemoryCommand:
    mutation = MemoryMutation(
        action="UPSERT",
        candidate=MemoryCandidate(
            memory_key=proposal.candidate.memory_key,
            category=MemoryCategory(proposal.candidate.category),
            content=FactContent(value=proposal.candidate.value),
        ),
        expected_version=proposal.expected_version,
        restore=row.is_deleted if row else False,
        enable=not row.enabled if row else False,
    )
    return MemoryCommand(
        command_id=f"confirmation:{interrupt_id}:{source_id}",
        endpoint="memory.confirmation",
        mutations=[mutation],
    )


async def _saved_result(session: AsyncSession, run: RunRecord, key: str) -> ConfirmationResult:
    stored = await session.scalar(
        select(UserMemory)
        .where(
            UserMemory.tenant_id == run.tenant_id,
            UserMemory.user_id == run.user_id,
            UserMemory.memory_key == key,
        )
        .execution_options(populate_existing=True)
    )
    if stored is None:
        raise RuntimeError("committed confirmation must have a fact")
    return ConfirmationResult(
        outcome="CONFIRMED",
        saved=True,
        memory_key=stored.memory_key,
        version=stored.version,
        expires_at=stored.expires_at,
    )


def _ensure_deadline(run: RunRecord, now: datetime) -> None:
    if run.deadline_at is None or run.deadline_at <= now:
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_EXPIRED)


def _result_text(result: ConfirmationResult) -> str:
    if result.outcome == "STALE":
        return "原提议已失效，未保存记忆；请重新展示候选后确认。\n" + result.model_dump_json()
    if result.outcome == "REJECTED":
        return "已拒绝保存该记忆。\n" + result.model_dump_json()
    return "记忆已保存。\n" + result.model_dump_json()
