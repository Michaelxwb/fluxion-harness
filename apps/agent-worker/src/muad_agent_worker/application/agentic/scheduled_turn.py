"""AGENT_TURN assembly: freeze the schedule facts and render the unattended prompt.

The prompt is rendered from the frozen occurrence (``task.scheduled_at`` / revision timezone)
and the Schedule's state (last meaningful result / scratch). It never uses the process wall
clock, so retries of one occurrence keep the same time line and window.

Session targets decide context only: ``isolated`` starts clean, ``current`` reads a bounded tail
of the creation conversation when its identity is available, ``session:<id>`` requires that
conversation to exist, and ``main`` uses the owner's main conversation when recorded. A missing
or archived session fails explicitly; the runtime never picks a substitute.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from muad_agent_core.model import ModelMessage, ModelRole
from muad_agent_core.prompt import (
    TurnWindow,
    local_day_window,
    render_scheduled_turn_prompt,
    render_time_variables,
    resolve_zone,
)
from muad_contracts import ScheduleWindow, SessionTarget, TriggerType
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...infrastructure.models.task import (
    TaskSchedule,
    TaskScheduleDraft,
    TaskScheduleRevision,
)
from .errors import AgenticError
from .memory import frozen_memory_message
from .session_context import (
    MAX_SESSION_MESSAGES,
    SessionContextReader,
    SessionUnavailable,
)
from .snapshot import FrozenAgenticExecution

logger = logging.getLogger("muad_agent_worker.application.agentic.scheduled_turn")

DEFAULT_SESSION_TARGET = "isolated"
SESSION_CONTEXT_UNAVAILABLE = "SESSION_CONTEXT_UNAVAILABLE"
SESSION_UNAVAILABLE = "SESSION_UNAVAILABLE"
SCHEDULE_WINDOW_INVALID = "SCHEDULE_WINDOW_INVALID"
_PREVIOUS_DAY_VARIABLE = re.compile(r"\{\{\s*previous_day\s*\}\}")


@dataclass(frozen=True, slots=True)
class ScheduledTurn:
    """One rendered unattended turn; frozen to this occurrence."""

    schedule_id: UUID
    name: str
    timezone: str
    scheduled_at: datetime
    window: TurnWindow
    prompt: str
    history: tuple[ModelMessage, ...] = ()
    session_target: str = DEFAULT_SESSION_TARGET
    scratch_revision: int = 0


async def load_scheduled_turn(
    sessions: async_sessionmaker[AsyncSession],
    frozen: FrozenAgenticExecution,
    reader: SessionContextReader | None,
) -> ScheduledTurn | None:
    """Load/rendered turn for a scheduled AGENT_TURN; immediate executions have none."""
    task = frozen.task
    scheduled = task.trigger_type == str(TriggerType.SCHEDULED)
    payload = task.input_json if isinstance(task.input_json, Mapping) else {}
    trial_draft_id = payload.get("trial_draft_id")
    if not scheduled and not isinstance(trial_draft_id, str):
        return None
    if task.scheduled_at is None:
        raise AgenticError(
            "SCHEDULE_TIME_MISSING",
            "execution has no frozen occurrence time",
            retryable=False,
        )
    if scheduled:
        if task.schedule_revision_id is None:
            raise AgenticError(
                "SCHEDULE_REVISION_MISSING",
                "scheduled execution has no frozen schedule revision",
                retryable=False,
            )
        async with sessions() as session:
            schedule = await session.scalar(
                select(TaskSchedule).where(
                    TaskSchedule.id == task.schedule_id,
                    TaskSchedule.tenant_id == task.tenant_id,
                    TaskSchedule.is_deleted.is_(False),
                )
            )
            revision = await session.scalar(
                select(TaskScheduleRevision).where(
                    TaskScheduleRevision.id == task.schedule_revision_id,
                    TaskScheduleRevision.tenant_id == task.tenant_id,
                    TaskScheduleRevision.is_deleted.is_(False),
                )
            )
        if schedule is None:
            raise AgenticError(
                "SCHEDULE_MISSING",
                "scheduled execution references a missing schedule",
                retryable=False,
            )
        if revision is None:
            raise AgenticError(
                "SCHEDULE_REVISION_MISSING",
                "scheduled execution references a missing revision",
                retryable=False,
            )
        timezone = revision.timezone
        job_id = schedule.id
        job_name = schedule.name
        last_result = schedule.last_result_json
        scratch = schedule.scratch_json
        scratch_revision = int(schedule.scratch_revision or 0)
    else:
        # 试跑与定时执行共用同一份 prompt 契约；没有 Schedule/Revision，只读 Draft 的配置。
        async with sessions() as session:
            draft = await session.scalar(
                select(TaskScheduleDraft).where(
                    TaskScheduleDraft.id == UUID(str(trial_draft_id)),
                    TaskScheduleDraft.tenant_id == task.tenant_id,
                    TaskScheduleDraft.is_deleted.is_(False),
                )
            )
        if draft is None:
            raise AgenticError(
                "SCHEDULE_DRAFT_NOT_FOUND",
                "trial execution references a missing draft",
                retryable=False,
            )
        body = draft.payload_json if isinstance(draft.payload_json, Mapping) else {}
        recurrence = body.get("recurrence") if isinstance(body.get("recurrence"), Mapping) else {}
        raw_timezone = recurrence.get("timezone")
        if not isinstance(raw_timezone, str) or not raw_timezone:
            raise AgenticError(
                "SCHEDULE_TIMEZONE_INVALID",
                "trial draft has no timezone",
                retryable=False,
            )
        timezone = raw_timezone
        job_id = draft.id
        job_name = str(body.get("name") or "试运行")
        last_result = None
        scratch = None
        scratch_revision = 0
    try:
        zone = resolve_zone(timezone)
    except ValueError as exc:
        raise AgenticError(
            "SCHEDULE_TIMEZONE_INVALID", str(exc), retryable=False
        ) from exc
    context = dict(frozen.fire_context)
    # 未显式声明窗口时：提示里的 {{previous_day}} 表示覆盖昨天，否则覆盖触发当天。
    window = _window(
        _window_value(payload) or _window_value(context),
        scheduled_at=task.scheduled_at,
        timezone=zone.key,
        default_offset=-1 if _PREVIOUS_DAY_VARIABLE.search(frozen.goal) else 0,
    )
    try:
        prompt = render_time_variables(
            frozen.goal, at=task.scheduled_at, timezone=zone.key
        )
    except ValueError as exc:
        raise AgenticError("AGENTIC_INPUT_INVALID", str(exc), retryable=False) from exc
    rendered = render_scheduled_turn_prompt(
        prompt=prompt,
        job_id=str(job_id),
        name=job_name,
        scheduled_at=task.scheduled_at,
        timezone=zone.key,
        window=window,
        previous_result=last_result,
        scratch=scratch,
        scratch_revision=scratch_revision,
    )
    target = _session_target(frozen)
    history = await _session_history(
        reader,
        target=target,
        context=context,
        tenant_id=task.tenant_id,
        owner_user_id=task.actor_user_id,
        agent_id=task.agent_id,
    )
    return ScheduledTurn(
        schedule_id=job_id,
        name=job_name,
        timezone=zone.key,
        scheduled_at=task.scheduled_at,
        window=window,
        prompt=rendered,
        history=history,
        session_target=str(target),
        scratch_revision=scratch_revision,
    )


def base_messages(
    frozen: FrozenAgenticExecution, turn: ScheduledTurn | None
) -> tuple[ModelMessage, ...]:
    """Start messages: read-only memory, optional session tail, then this occurrence's goal."""
    memory = frozen_memory_message(frozen.memory_block)
    goal = ModelMessage(
        role=ModelRole.USER, content=turn.prompt if turn is not None else frozen.goal
    )
    head: list[ModelMessage] = []
    if memory is not None:
        head.append(memory)
    if turn is not None and turn.history:
        head.extend(turn.history)
    head.append(goal)
    return tuple(head)


def _session_target(frozen: FrozenAgenticExecution) -> SessionTarget:
    payload = frozen.task.input_json if isinstance(frozen.task.input_json, Mapping) else {}
    raw = payload.get("session_target", DEFAULT_SESSION_TARGET)
    if not isinstance(raw, str) or not raw:
        raise AgenticError(
            "AGENTIC_INPUT_INVALID", "session_target must be a non-empty string", retryable=False
        )
    try:
        return SessionTarget(raw)
    except ValueError as exc:
        raise AgenticError("AGENTIC_INPUT_INVALID", str(exc), retryable=False) from exc


def _session_conversation_id(target: SessionTarget, context: Mapping[str, Any]) -> UUID | None:
    """Resolve the frozen conversation identity for current/main; session:<id> carries its own.

    Authoring can store it as `conversation_id` / `main_conversation_id` at the top level or
    under `parameters`/`source`/`output`. Missing identity means the optional tail is simply
    unavailable (except for an explicit `session:<id>`, which always requires the reader).
    """
    if target.kind == "isolated":
        return None
    if target.kind == "session":
        return target.session_id
    key = "main_conversation_id" if target.kind == "main" else "conversation_id"
    candidates: list[Any] = [
        context.get(key),
        context.get("conversation_id"),
        context.get("session_conversation_id"),
    ]
    for nested in ("parameters", "source", "output"):
        value = context.get(nested)
        if isinstance(value, Mapping):
            candidates.extend((value.get(key), value.get("conversation_id")))
    for raw in candidates:
        if isinstance(raw, str) and raw.strip():
            try:
                return UUID(raw.strip())
            except ValueError as exc:
                raise AgenticError(
                    "AGENTIC_INPUT_INVALID",
                    f"session target {target} carries an invalid conversation id",
                    retryable=False,
                ) from exc
    return None


async def _session_history(
    reader: SessionContextReader | None,
    *,
    target: SessionTarget,
    context: Mapping[str, Any],
    tenant_id: str,
    owner_user_id: UUID,
    agent_id: UUID,
) -> tuple[ModelMessage, ...]:
    conversation_id = _session_conversation_id(target, context)
    if conversation_id is None:
        if target.kind == "session":
            raise AgenticError(
                SESSION_CONTEXT_UNAVAILABLE,
                f"session target {target} has no conversation identity",
                retryable=False,
            )
        if target.kind in ("current", "main"):
            logger.info(
                "scheduled_session_context_unavailable",
                extra={"tenant_id": tenant_id, "session_target": str(target)},
            )
        return ()
    if reader is None:
        if target.kind == "session":
            raise AgenticError(
                SESSION_CONTEXT_UNAVAILABLE,
                f"session target {target} cannot be read",
                retryable=False,
            )
        logger.warning(
            "scheduled_session_reader_missing",
            extra={"tenant_id": tenant_id, "session_target": str(target)},
        )
        return ()
    try:
        return await reader.tail(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            agent_id=agent_id,
            session_id=conversation_id,
            limit=MAX_SESSION_MESSAGES,
        )
    except SessionUnavailable as exc:
        raise AgenticError(SESSION_UNAVAILABLE, str(exc), retryable=False) from exc


def _window(
    raw: Any,
    *,
    scheduled_at: datetime,
    timezone: str,
    default_offset: int,
) -> TurnWindow:
    if raw is None:
        return local_day_window(scheduled_at, timezone, default_offset)
    try:
        window = ScheduleWindow.model_validate(raw)
    except ValueError as exc:
        raise AgenticError(SCHEDULE_WINDOW_INVALID, str(exc), retryable=False) from exc
    return local_day_window(scheduled_at, timezone, window.offset)


def _window_value(context: Mapping[str, Any]) -> Any:
    value = context.get("window")
    if value is not None:
        return value
    for nested in ("parameters", "output"):
        child = context.get(nested)
        if isinstance(child, Mapping) and child.get("window") is not None:
            return child["window"]
    return None


__all__ = ["ScheduledTurn", "base_messages", "load_scheduled_turn"]
