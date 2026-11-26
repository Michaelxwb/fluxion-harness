"""User-visible failure when maintenance, not a live execution owner, closes a Run."""

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from ...infrastructure.models.runtime import CanonicalEvent, RunRecord, RunSubmission, RuntimeSnapshot
from ..channel_notifications import KIND_RUN_COMPLETED, run_terminal_delivery_key, write_run_notification
from ..run_events import EventAppend, EventWriter, delta_payload
from .facts import build_finalization_facts
from .render import render_finalization, render_technical_details


async def frozen_run_locale(session: AsyncSession, run: RunRecord) -> str | None:
    if run.snapshot_id is None:
        return None
    policy = await session.scalar(sa.select(RuntimeSnapshot.policy_json).where(
        RuntimeSnapshot.id == run.snapshot_id, RuntimeSnapshot.tenant_id == run.tenant_id,
    ))
    locale = (policy or {}).get("locale")
    value = locale.get("default_locale") if isinstance(locale, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


async def partial_submission_reply(
    session: AsyncSession, run: RunRecord, submission_id: uuid.UUID,
) -> tuple[str, int]:
    event = CanonicalEvent
    rows = await session.execute(sa.select(event.stream_type, event.payload_json).where(
        event.tenant_id == run.tenant_id, event.run_id == run.id,
        event.submission_id == submission_id, event.is_deleted.is_(False),
        event.stream_type.in_(("message.snapshot", "message.delta")),
    ).order_by(event.seq))
    generation = 0
    chunks: list[str] = []
    for kind, payload in rows:
        current = payload.get("reply_generation", 0)
        if type(current) is not int or current < generation:
            continue
        if current > generation or kind == "message.snapshot":
            generation = current
            chunks.clear()
        text = payload.get("text" if kind == "message.snapshot" else "delta")
        if isinstance(text, str):
            chunks.append(text)
    return "".join(chunks), generation


async def write_maintenance_failure(session: AsyncSession, run: RunRecord, code: str) -> int:
    submission_id = await session.scalar(sa.select(RunSubmission.id).where(
        RunSubmission.tenant_id == run.tenant_id, RunSubmission.run_id == run.id,
        RunSubmission.status == "OPEN", RunSubmission.is_deleted.is_(False),
    ).order_by(RunSubmission.create_time.desc(), RunSubmission.id).limit(1))
    partial, generation = (
        await partial_submission_reply(session, run, submission_id) if submission_id else ("", 0)
    )
    locale = await frozen_run_locale(session, run)
    facts = build_finalization_facts(error_code=code, snapshot=None)
    suffix = render_finalization(facts, locale=locale, include_technical=False)
    delta = f"\n\n{suffix}" if partial else suffix
    text = partial + delta
    details = render_technical_details(facts, locale=locale, run_id=str(run.id))
    terminal_event_id = uuid.uuid4()
    source = (run.tenant_id, run.conversation_id, run.id)
    terminal = {"status": "FAILED", "error_code": code}
    _, _, seq = await EventWriter(session, allow_deleted_conversation=True).append_many((
        EventAppend(*source, "ASSISTANT_MESSAGE", {"text": text, "technical_details": details},
                    submission_id=submission_id),
        EventAppend(*source, "ASSISTANT_DELTA", delta_payload(delta, reply_generation=generation),
                    submission_id=submission_id, stream_type="message.delta"),
        EventAppend(*source, "RUN_FAILED", {**terminal, "final_text": text, "technical_details": details},
                    submission_id=submission_id, stream_type="run.failed", event_id=terminal_event_id),
    ))
    await write_run_notification(
        session, run=run, kind=KIND_RUN_COMPLETED, delivery_key=run_terminal_delivery_key(run.id),
        event_id=terminal_event_id, push_messages=True,
    )
    return seq
