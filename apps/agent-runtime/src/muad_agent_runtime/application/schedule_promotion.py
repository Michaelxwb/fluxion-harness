"""Promote a successful skill call into a Schedule Draft (design §10.5 / §10.11 / §10.15).

The promotion path exists because the most accurate schedule is the one derived from a
real, successful execution — not from prose. Two facts are read from the run itself:

- **what succeeded**: `runtime.tool_call_audit` must record a business `SUCCEEDED`
  `execute(target=skill)` / `execute_skill` call with a completed tool result;
- **with which arguments**: the canonical `ASSISTANT_TURN` event keeps the model's full
  tool-call arguments (the audit only keeps a redacted 200-char preview), so `input` and
  `argv` are copied exactly instead of re-inferred.

Date-like values are then bound to the scheduler's whitelisted variables (`{{previous_day}}`,
`{{fire_date}}`) relative to the recorded call time in the schedule timezone. Everything
else is left literal. The result is still a Draft: the user confirms it explicitly, and
nothing is activated by promotion.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactoryProvider
from ..infrastructure.models.runtime import CanonicalEvent, RunRecord, ToolCallAudit

PROMOTE_SKILL_TOOLS = ("execute", "execute_skill")
SUCCESS_STATUS = "SUCCEEDED"
TOOL_COMPLETED_EVENT = "TOOL_CALL"
ASSISTANT_TURN_EVENT = "ASSISTANT_TURN"
DATE_VARIABLE = "previous_day"
FIRE_DATE_VARIABLE = "fire_date"

_DATE_TOKEN = re.compile(r"(?<![0-9A-Za-z_])(\d{4}-\d{2}-\d{2})(?![0-9A-Za-z_])")
#: Only standalone date tokens are business parameters; ids/versions that merely contain a
#: date must stay literal.
_DATE_LEFT = frozenset(" \t\r\n=:,([{\"'")
_DATE_RIGHT = frozenset(" \t\r\n],)};\"',")


class PromotionError(RuntimeError):
    """Stable promotion failure: code + user-actionable data; never a guessed success."""

    def __init__(self, code: str, message: str, data: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = dict(data or {})


@dataclass(frozen=True, slots=True)
class PromotionFacts:
    """The recorded call used as the promotion source; read facts, not guesses."""

    run_id: uuid.UUID
    tool_call_id: str
    skill_key: str | None
    skill_id: str | None
    input: dict[str, Any]
    argv: tuple[str, ...]
    executed_at: datetime
    conversation_id: uuid.UUID
    #: `{variable: count}` for the date values that were actually bound.
    bindings: dict[str, int] = field(default_factory=dict)

    @property
    def skill_identifier(self) -> str:
        return self.skill_key or self.skill_id or ""


def _bind_text(text: str, dates: Mapping[str, str], hits: dict[str, int]) -> str:
    """Bind exact date values inside one string; time-bearing values stay literal."""

    def replace(match: re.Match[str]) -> str:
        value = match.group(1)
        variable = dates.get(value)
        if variable is None:
            return match.group(0)
        if match.start() > 0 and text[match.start() - 1] not in _DATE_LEFT:
            return match.group(0)
        if match.end() < len(text) and text[match.end()] not in _DATE_RIGHT:
            return match.group(0)
        hits[variable] = hits.get(variable, 0) + 1
        return "{{" + variable + "}}"

    return _DATE_TOKEN.sub(replace, text)


def bind_dates(value: Any, *, dates: Mapping[str, str], hits: dict[str, int]) -> Any:
    """Recursively bind date values in `input`/`argv`; keys are left untouched."""
    if isinstance(value, str):
        return _bind_text(value, dates, hits)
    if isinstance(value, Mapping):
        return {key: bind_dates(item, dates=dates, hits=hits) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [bind_dates(item, dates=dates, hits=hits) for item in value]
    return value


def date_bindings(executed_at: datetime, timezone: str) -> dict[str, str]:
    """Recorded call's local `fire_date`/`previous_day` → whitelisted variable names."""
    local = executed_at.astimezone(ZoneInfo(timezone))
    return {
        local.date().isoformat(): FIRE_DATE_VARIABLE,
        (local.date() - timedelta(days=1)).isoformat(): DATE_VARIABLE,
    }


def window_for(bindings: Mapping[str, int]) -> dict[str, Any]:
    """Data window follows the bound date: `previous_day` means offset -1."""
    return {"kind": "DAY", "offset": -1 if DATE_VARIABLE in bindings else 0}


def bind_call(facts: PromotionFacts, *, timezone: str) -> PromotionFacts:
    """Return promotion facts with dates bound inside `input` and `argv`.

    An invalid timezone is left to the authoring compiler (it owns the field error), so the
    binding falls back to UTC here instead of raising before the model gets a repair hint.
    """
    hits: dict[str, int] = {}
    try:
        dates = date_bindings(facts.executed_at, timezone)
    except (ValueError, ZoneInfoNotFoundError):
        dates = date_bindings(facts.executed_at, "UTC")
    bound_input = bind_dates(facts.input, dates=dates, hits=hits)
    bound_argv = bind_dates(list(facts.argv), dates=dates, hits=hits)
    return PromotionFacts(
        run_id=facts.run_id,
        tool_call_id=facts.tool_call_id,
        skill_key=facts.skill_key,
        skill_id=facts.skill_id,
        input=dict(bound_input) if isinstance(bound_input, Mapping) else {},
        argv=tuple(str(item) for item in bound_argv),
        executed_at=facts.executed_at,
        conversation_id=facts.conversation_id,
        bindings=hits,
    )


def skill_payload(facts: PromotionFacts, *, window: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """`SchedulePlan.payload` for the recorded call; skill identity and argv stay exact."""
    payload: dict[str, Any] = {
        "kind": "SKILL_PLAN",
        "input": dict(facts.input),
        "argv": list(facts.argv),
        "window": dict(window or window_for(facts.bindings)),
    }
    if facts.skill_key:
        payload["skill_key"] = facts.skill_key
    elif facts.skill_id:
        payload["skill_id"] = facts.skill_id
    return payload


@dataclass(frozen=True, slots=True)
class PromotionProposal:
    """Compiled tool arguments + the exact bound facts shown in the receipt."""

    arguments: dict[str, Any]
    facts: PromotionFacts


def promotion_proposal(
    facts: PromotionFacts,
    arguments: Mapping[str, Any],
    *,
    timezone: str,
) -> PromotionProposal:
    """Tool arguments for the authoring compiler: recorded skill facts + user schedule facts.

    The model supplies only scheduling/display fields (`instruction`/`timezone`/`recurrence`/
    `session_target`/`delivery`); `payload` is derived from the recorded call, never from prose.
    """
    bound = bind_call(facts, timezone=timezone)
    raw_window = arguments.get("window")
    window = raw_window if isinstance(raw_window, Mapping) else None
    payload = skill_payload(bound, window=window)
    reason = arguments.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        reason = (
            f"Promoted from the successful run {bound.run_id} "
            f"({bound.tool_call_id}, skill {bound.skill_identifier})."
        )
    compiled = {
        **{key: value for key, value in arguments.items() if key not in {"window", "reason"}},
        "payload": payload,
        "confidence": 1.0,
        "reason": reason,
    }
    return PromotionProposal(arguments=compiled, facts=bound)


def _tool_arguments(call: Mapping[str, Any]) -> tuple[str | None, str | None, dict[str, Any], list[str]]:
    """Validate one recorded skill call; explicit failure instead of a silent downgrade."""
    args = call.get("arguments")
    if not isinstance(args, Mapping):
        raise PromotionError(
            "PROMOTION_SOURCE_INVALID", "the recorded tool call has no argument object"
        )
    name = call.get("name")
    target = args.get("target")
    if name == "execute" and target != "skill":
        raise PromotionError(
            "PROMOTION_SOURCE_INVALID", "the recorded call did not target a skill"
        )
    if name not in PROMOTE_SKILL_TOOLS:
        raise PromotionError(
            "PROMOTION_SOURCE_INVALID", f"tool {name!r} cannot be promoted to a schedule"
        )
    if "script" in args:
        raise PromotionError(
            "PROMOTION_SOURCE_INVALID",
            "a script debug call cannot be promoted; promote a regular skill execution",
        )
    skill_key = args.get("skill_key")
    skill_id = args.get("skill_id")
    if not isinstance(skill_key, str) or not skill_key.strip():
        skill_key = None
    if not isinstance(skill_id, str) or not skill_id.strip():
        skill_id = None
    if skill_key is None and skill_id is None:
        raise PromotionError(
            "PROMOTION_SOURCE_INVALID", "the recorded call has no skill identity"
        )
    raw_input = args.get("input")
    if raw_input is not None and not isinstance(raw_input, Mapping):
        raise PromotionError("PROMOTION_SOURCE_INVALID", "recorded skill input is not an object")
    raw_argv = args.get("argv")
    if raw_argv is not None and (
        not isinstance(raw_argv, list) or any(not isinstance(item, str) for item in raw_argv)
    ):
        raise PromotionError("PROMOTION_SOURCE_INVALID", "recorded skill argv is not a string array")
    return skill_key, skill_id, dict(raw_input or {}), list(raw_argv or [])


def _completed_calls(events: list[CanonicalEvent]) -> tuple[dict[str, list[Mapping[str, Any]]], set[str]]:
    calls: dict[str, list[Mapping[str, Any]]] = {}
    completed: set[str] = set()
    for event in events:
        payload = event.payload_json or {}
        if event.event_type == ASSISTANT_TURN_EVENT:
            raw_calls = payload.get("tool_calls")
            if not isinstance(raw_calls, list):
                continue
            for item in raw_calls:
                if not isinstance(item, Mapping):
                    continue
                call_id = item.get("id")
                if isinstance(call_id, str) and call_id:
                    calls.setdefault(call_id, []).append(item)
        elif event.event_type == TOOL_COMPLETED_EVENT:
            call_id = payload.get("tool_call_id")
            if isinstance(call_id, str):
                completed.add(call_id)
    return calls, completed


async def _owned_run(
    session: AsyncSession,
    *,
    tenant_id: str,
    actor_user_id: uuid.UUID,
    agent_id: uuid.UUID,
    run_id: uuid.UUID,
) -> RunRecord:
    run = await session.scalar(
        select(RunRecord).where(
            RunRecord.id == run_id,
            RunRecord.tenant_id == tenant_id,
            RunRecord.user_id == actor_user_id,
            RunRecord.agent_id == agent_id,
            RunRecord.is_deleted.is_(False),
        )
    )
    if run is None:
        raise PromotionError("COMMON_NOT_FOUND", "run not found")
    if run.status != "COMPLETED" or run.error_code is not None:
        raise PromotionError(
            "RUN_NOT_COMPLETED",
            "only a completed run can be promoted",
            {"status": str(run.status), "error_code": run.error_code},
        )
    return run


async def _run_audits(
    session: AsyncSession, *, tenant_id: str, run_id: uuid.UUID
) -> list[ToolCallAudit]:
    return list(
        (
            await session.scalars(
                select(ToolCallAudit)
                .where(
                    ToolCallAudit.tenant_id == tenant_id,
                    ToolCallAudit.run_id == run_id,
                    ToolCallAudit.tool_name.in_(PROMOTE_SKILL_TOOLS),
                    ToolCallAudit.is_deleted.is_(False),
                )
                .order_by(ToolCallAudit.start_time.desc(), ToolCallAudit.create_time.desc())
            )
        ).all()
    )


async def _run_events(
    session: AsyncSession, *, tenant_id: str, run_id: uuid.UUID
) -> list[CanonicalEvent]:
    return list(
        (
            await session.scalars(
                select(CanonicalEvent)
                .where(
                    CanonicalEvent.tenant_id == tenant_id,
                    CanonicalEvent.run_id == run_id,
                    CanonicalEvent.event_type.in_((ASSISTANT_TURN_EVENT, TOOL_COMPLETED_EVENT)),
                    CanonicalEvent.is_deleted.is_(False),
                )
                .order_by(CanonicalEvent.seq.asc())
            )
        ).all()
    )


def _successful_call(
    run: RunRecord, audits: list[ToolCallAudit], events: list[CanonicalEvent]
) -> PromotionFacts:
    calls, completed = _completed_calls(events)
    for audit in audits:
        if audit.business_status != SUCCESS_STATUS or audit.status != "OK":
            continue
        if audit.tool_call_id not in completed:
            continue
        for call in calls.get(audit.tool_call_id, ()):
            if call.get("name") != audit.tool_name:
                continue
            skill_key, skill_id, input_data, argv = _tool_arguments(call)
            return PromotionFacts(
                run_id=run.id,
                tool_call_id=audit.tool_call_id,
                skill_key=skill_key,
                skill_id=skill_id,
                input=input_data,
                argv=tuple(argv),
                executed_at=audit.start_time,
                conversation_id=run.conversation_id,
            )
    raise PromotionError(
        "RUN_SKILL_CALL_MISSING",
        "the run has no successful skill execution to promote",
    )


async def load_promotion_facts(
    session_factory: SessionFactoryProvider,
    *,
    tenant_id: str,
    actor_user_id: uuid.UUID,
    agent_id: uuid.UUID,
    run_id: uuid.UUID,
) -> PromotionFacts:
    """Read one successful skill call from a Run owned by exactly this user and Agent.

    Cross-tenant / cross-user / cross-agent ids are indistinguishable from a missing run.
    """
    async with session_factory()() as session:
        run = await _owned_run(
            session,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            agent_id=agent_id,
            run_id=run_id,
        )
        audits = await _run_audits(session, tenant_id=tenant_id, run_id=run_id)
        events = await _run_events(session, tenant_id=tenant_id, run_id=run_id)
    return _successful_call(run, audits, events)


__all__ = [
    "DATE_VARIABLE",
    "FIRE_DATE_VARIABLE",
    "PROMOTE_SKILL_TOOLS",
    "PromotionError",
    "PromotionFacts",
    "PromotionProposal",
    "bind_call",
    "bind_dates",
    "date_bindings",
    "load_promotion_facts",
    "promotion_proposal",
    "skill_payload",
    "window_for",
]
