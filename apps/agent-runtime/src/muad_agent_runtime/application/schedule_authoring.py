"""Authoring compiler: turn a model proposal into one validated `SchedulePlan`.

The model only describes the business intent. This module is the deterministic boundary:

- `kind` is derived from the payload shape when omitted; a mismatch between an explicit
  kind and the fields is an explicit error, never a silent downgrade;
- Skill/entrypoint/input/argv/window/tools are checked against the *already resolved* Run
  materials (`ResolvedSkill` / `ResolvedMcpServer`). File existence, grants and READY
  artifacts stay owned by Worker admission, which re-resolves them when the Draft is
  created — this module never becomes a second materials resolver;
- ambiguous intent returns one business question instead of asking the user to pick an
  internal mode;
- every rejection is a structured `{field, reason, repair, allowed_values?}` list.

Authoring metadata (`confidence`/`reason`) is returned beside the plan and is never written
into the frozen `ScheduleJob` configuration.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from muad_api.error_codes import ErrorCode
from muad_contracts import (
    AgentTurnPayload,
    DeliveryPlan,
    DeliveryRouteInput,
    ReminderPayload,
    ResolvedMcpServer,
    ResolvedSkill,
    SchedulePayload,
    SchedulePlan,
    ScheduleWindow,
    SessionTarget,
    SkillPlanPayload,
)
from muad_contracts.enums import (
    LOCAL_STREAM_CHANNELS,
    OUTBOUND_CHANNELS,
    DeliveryIntent,
)
from pydantic import ValidationError

from .time_tools import resolve_zone

#: Below this confidence the compiler asks one business question instead of drafting.
_clarify_below = 0.6
#: The only dynamic window variables understood by the scheduler (Worker owns rendering).
_window_variables = frozenset({"fire_date", "previous_day", "fire_time"})
_variable_pattern = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
#: Platform tools that are always part of the owner's tool surface.
_platform_tools = frozenset(
    {
        "current_time",
        "remember",
        "recall",
        "forget",
        "read_attachment",
        "search_attachment",
        "list_attachments",
        "view_image",
        "write_artifact",
        "append_artifact",
        "deliver_artifact",
        "create_archive",
        "load_skill",
        "read_skill_resource",
        "execute_skill",
        "run_skill_script",
        "search_skills",
        "execute",
        "get_task",
        "list_tasks",
        "cancel_task",
        "get_schedule",
        "list_schedules",
        "pause_schedule",
        "resume_schedule",
        "cancel_schedule",
    }
)
_script_suffixes = frozenset({".py", ".js", ".mjs", ".cjs"})
#: Control fields belong to the envelope, never to the Skill's stdin business input.
_control_input_keys = frozenset(
    {"kind", "skill_id", "skill_key", "argv", "window", "mode", "instruction", "recurrence"}
)
_payload_fields: dict[str, tuple[str, ...]] = {
    "REMINDER": ("text",),
    "SKILL_PLAN": ("skill_key", "skill_id", "input", "argv", "window"),
    "AGENT_TURN": ("prompt", "model_ref", "tools_allow", "budget_ref"),
}
_delivery_reasons = frozenset(
    {
        "DELIVERY_ROUTE_UNAVAILABLE",
        "DELIVERY_CHANNEL_UNSUPPORTED",
        "ENTRY_CHANNEL_UNSUPPORTED",
        "ROUTE_NOT_ALLOWED",
        "ROUTE_NOT_OWNED",
    }
)


@dataclass(frozen=True, slots=True)
class ScheduleFieldError:
    """One actionable rejection; the model can repair exactly this field once."""

    field: str
    reason: str
    repair: str
    allowed_values: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "field": self.field,
            "reason": self.reason,
            "repair": self.repair,
        }
        if self.allowed_values:
            data["allowed_values"] = list(self.allowed_values)
        return data


@dataclass(frozen=True, slots=True)
class ScheduleClarification:
    """A business question for ambiguous intent; never a mode picker."""

    question: str
    reason: str
    fields: tuple[str, ...] = ()
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class DeliveryTargetOption:
    """One owner-scoped outbound target; only the safe label and route shape."""

    label: str
    route: DeliveryRouteInput


@dataclass(frozen=True, slots=True)
class ScheduleAuthoringContext:
    """Resolved Run materials the compiler may inspect without another resolver round trip."""

    skills: Mapping[str, ResolvedSkill]
    mcp_servers: Sequence[ResolvedMcpServer] = ()
    entry_channel: str | None = None
    delivery_route: DeliveryRouteInput | None = None
    delivery_targets: Sequence[DeliveryTargetOption] = ()
    conversation_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ScheduleAuthoringResult:
    """A validated plan, one clarification question, or both when the plan is tentative."""

    plan: SchedulePlan | None
    confidence: float
    reason: str
    budget_source: str
    timezone: str | None
    recurrence: dict[str, Any] | None
    clarification: ScheduleClarification | None = None

    @property
    def resolved_payload(self) -> dict[str, Any] | None:
        return self.plan.payload.model_dump(mode="json") if self.plan is not None else None

    @property
    def tools_allow(self) -> list[str]:
        return self.plan.resolved_tools().tools_allow if self.plan is not None else []


class ScheduleAuthoringError(RuntimeError):
    """Structured validation failure: stable code, message and field-level repairs."""

    def __init__(
        self, code: str, message: str, fields: Sequence[ScheduleFieldError]
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fields = tuple(fields)
        self.data: dict[str, Any] = {"fields": [item.as_dict() for item in self.fields]}
        if self.fields:
            self.data["reason"] = self.fields[0].reason
            self.data["repair"] = self.fields[0].repair


def compile_schedule_plan(
    arguments: Mapping[str, Any], *, context: ScheduleAuthoringContext
) -> ScheduleAuthoringResult:
    """Compile tool arguments into `SchedulePlan`, a clarification, or field errors."""
    errors: list[ScheduleFieldError] = []
    confidence = _confidence(arguments, errors)
    timezone = _timezone(arguments, errors)
    recurrence = _recurrence(arguments, timezone, errors)
    raw = arguments.get("payload")
    payload: Mapping[str, Any] | None
    if raw is None:
        payload = None
    elif isinstance(raw, Mapping):
        payload = raw
    else:
        payload = None
        errors.append(_field("payload", "INVALID_SHAPE", "PROVIDE_PAYLOAD_OBJECT"))
    kind = _payload_kind(payload, errors) if payload is not None else None
    plan = _plan(payload, kind, context, arguments, errors)
    if errors:
        raise _failure(errors)
    if plan is None:
        return _clarification_result(kind, confidence, timezone, recurrence)
    reason = _reason(arguments, plan, errors)
    if errors:
        raise _failure(errors)
    if confidence < _clarify_below:
        return _clarification_result(
            kind, confidence, timezone, recurrence, plan=plan, reason=reason
        )
    return ScheduleAuthoringResult(
        plan=plan,
        confidence=confidence,
        reason=reason,
        budget_source=_budget_source(plan),
        timezone=timezone,
        recurrence=recurrence,
    )


def _plan(
    payload: Mapping[str, Any] | None,
    kind: str | None,
    context: ScheduleAuthoringContext,
    arguments: Mapping[str, Any],
    errors: list[ScheduleFieldError],
) -> SchedulePlan | None:
    if kind is None:
        return None
    body: SchedulePayload | None
    if kind == "REMINDER":
        body = _reminder(payload or {}, errors)
    elif kind == "SKILL_PLAN":
        body = _skill_plan(payload or {}, context, errors)
    else:
        body = _agent_turn(payload or {}, context, errors)
    if body is None:
        return None
    delivery = _delivery(arguments, context, errors)
    session = _session(arguments, context, errors)
    if delivery is None or session is None:
        return None
    return SchedulePlan(payload=body, session_target=session, delivery=delivery)


def _confidence(arguments: Mapping[str, Any], errors: list[ScheduleFieldError]) -> float:
    value = arguments.get("confidence", 1.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(_field("confidence", "INVALID", "PROVIDE_NUMBER_0_TO_1"))
        return 1.0
    if not 0.0 <= float(value) <= 1.0:
        errors.append(_field("confidence", "OUT_OF_RANGE", "PROVIDE_NUMBER_0_TO_1"))
        return 1.0
    return float(value)


def _timezone(arguments: Mapping[str, Any], errors: list[ScheduleFieldError]) -> str | None:
    raw = arguments.get("timezone")
    if not isinstance(raw, str) or not raw.strip():
        errors.append(
            _field("timezone", "TIMEZONE_MISSING", "ASK_USER_TIMEZONE")
        )
        return None
    try:
        return resolve_zone(raw.strip()).key
    except ValueError:
        errors.append(
            _field("timezone", "INVALID_TIMEZONE", "PROVIDE_IANA_TIMEZONE")
        )
        return None


def _recurrence(
    arguments: Mapping[str, Any], timezone: str | None, errors: list[ScheduleFieldError]
) -> dict[str, Any] | None:
    raw = arguments.get("recurrence")
    if not isinstance(raw, Mapping):
        errors.append(_field("recurrence", "MISSING", "PROVIDE_RECURRENCE"))
        return None
    kind = raw.get("kind")
    if kind not in ("CRON", "ONCE"):
        errors.append(
            _field("recurrence.kind", "INVALID", "CHOOSE_RECURRENCE_KIND", ("CRON", "ONCE"))
        )
        return None
    if kind == "CRON":
        cron = raw.get("cron")
        if not isinstance(cron, str) or not cron.strip():
            errors.append(_field("recurrence.cron", "MISSING", "PROVIDE_FIVE_FIELD_CRON"))
            return None
        if len(cron.split()) != 5:
            errors.append(_field("recurrence.cron", "INVALID_CRON", "PROVIDE_FIVE_FIELD_CRON"))
            return None
        result: dict[str, Any] = {"kind": "CRON", "cron": cron.strip()}
    else:
        run_at = raw.get("run_at")
        if not isinstance(run_at, str) or not run_at.strip():
            errors.append(_field("recurrence.run_at", "MISSING", "PROVIDE_ISO8601_RUN_AT"))
            return None
        try:
            parsed = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
        except ValueError:
            errors.append(_field("recurrence.run_at", "INVALID", "PROVIDE_ISO8601_RUN_AT"))
            return None
        if parsed.tzinfo is None:
            errors.append(
                _field("recurrence.run_at", "INVALID", "PROVIDE_ISO8601_OFFSET")
            )
            return None
        result = {"kind": "ONCE", "run_at": run_at.strip()}
    if timezone is not None:
        result["timezone"] = timezone
    return result


def _payload_kind(
    payload: Mapping[str, Any], errors: list[ScheduleFieldError]
) -> str | None:
    kind = payload.get("kind")
    if kind is not None:
        if kind not in _payload_fields:
            errors.append(
                _field(
                    "payload.kind",
                    "INVALID_KIND",
                    "CHOOSE_PAYLOAD_KIND",
                    tuple(_payload_fields),
                )
            )
            return None
        for other, names in _payload_fields.items():
            if other == kind:
                continue
            for name in names:
                if payload.get(name) is not None:
                    errors.append(
                        _field(
                            f"payload.{name}",
                            "FIELD_NOT_ALLOWED",
                            f"REMOVE_OR_USE_{other}",
                        )
                    )
        return str(kind)
    active = [
        family
        for family, names in _payload_fields.items()
        if any(payload.get(name) is not None for name in names)
    ]
    if len(active) > 1:
        errors.append(
            _field(
                "payload.kind",
                "KIND_CONFLICT",
                "PROVIDE_ONLY_ONE_PAYLOAD_SHAPE",
                tuple(active),
            )
        )
        return None
    return active[0] if active else None


def _reminder(payload: Mapping[str, Any], errors: list[ScheduleFieldError]) -> ReminderPayload | None:
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        errors.append(_field("payload.text", "MISSING", "PROVIDE_REMINDER_TEXT"))
        return None
    try:
        return ReminderPayload(text=text.strip())
    except ValidationError as exc:
        errors.extend(_validation_errors("payload", exc))
        return None


def _skill_plan(
    payload: Mapping[str, Any],
    context: ScheduleAuthoringContext,
    errors: list[ScheduleFieldError],
) -> SkillPlanPayload | None:
    skill = _resolve_skill(payload, context, errors)
    prompt_input = _skill_input(payload, errors)
    argv = _skill_argv(payload, errors)
    window = _skill_window(payload, errors)
    if prompt_input is not None:
        _reject_unknown_variables(prompt_input, "payload.input", errors)
    if argv is not None:
        _reject_unknown_variables(argv, "payload.argv", errors)
    if skill is None or prompt_input is None or argv is None or window is None:
        return None
    try:
        return SkillPlanPayload(
            skill_id=skill.skill_id,
            input=prompt_input,
            argv=argv,
            window=window,
        )
    except ValidationError as exc:
        errors.extend(_validation_errors("payload", exc))
        return None


def _resolve_skill(
    payload: Mapping[str, Any],
    context: ScheduleAuthoringContext,
    errors: list[ScheduleFieldError],
) -> ResolvedSkill | None:
    skill = _match_skill(payload, context, errors)
    if skill is None:
        return None
    _check_declared_entrypoint(skill, errors)
    return skill


def _match_skill(
    payload: Mapping[str, Any],
    context: ScheduleAuthoringContext,
    errors: list[ScheduleFieldError],
) -> ResolvedSkill | None:
    key = payload.get("skill_key")
    identifier = payload.get("skill_id")
    allowed = tuple(sorted(context.skills))
    if isinstance(key, str) and key.strip():
        skill = context.skills.get(key.strip())
        if skill is not None:
            return skill
        errors.append(
            _field(
                "payload.skill_key",
                "SKILL_NOT_EFFECTIVE",
                "CHOOSE_EFFECTIVE_SKILL",
                allowed,
            )
        )
        return None
    if identifier is None:
        errors.append(
            _field("payload.skill_key", "MISSING", "PROVIDE_SKILL_KEY", allowed)
        )
        return None
    try:
        skill_id = uuid.UUID(str(identifier))
    except ValueError:
        errors.append(_field("payload.skill_id", "INVALID", "PROVIDE_SKILL_UUID"))
        return None
    skill = next(
        (item for item in context.skills.values() if item.skill_id == skill_id),
        None,
    )
    if skill is None:
        errors.append(
            _field(
                "payload.skill_id",
                "SKILL_NOT_EFFECTIVE",
                "CHOOSE_EFFECTIVE_SKILL",
                allowed,
            )
        )
    return skill


def _check_declared_entrypoint(
    skill: ResolvedSkill, errors: list[ScheduleFieldError]
) -> None:
    frontmatter = skill.frontmatter if isinstance(skill.frontmatter, Mapping) else {}
    for name in ("entrypoint", "default_script"):
        declared = frontmatter.get(name)
        if declared is None:
            continue
        if not isinstance(declared, str) or not declared.strip():
            errors.append(_entrypoint_error())
            return
        path = PurePosixPath(declared.strip().replace("\\", "/"))
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.suffix.lower() not in _script_suffixes
        ):
            errors.append(_entrypoint_error())
            return


def _entrypoint_error() -> ScheduleFieldError:
    return _field(
        "payload.skill_key",
        "ENTRYPOINT_INVALID",
        "REIMPORT_SKILL",
        tuple(sorted(_script_suffixes)),
    )


def _skill_input(
    payload: Mapping[str, Any], errors: list[ScheduleFieldError]
) -> dict[str, Any] | None:
    raw = payload.get("input")
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        errors.append(_field("payload.input", "INVALID", "PROVIDE_JSON_OBJECT"))
        return None
    control = sorted(_control_input_keys.intersection(raw))
    for key in control:
        errors.append(
            _field(
                f"payload.input.{key}",
                "CONTROL_FIELD_IN_INPUT",
                "MOVE_TO_PAYLOAD_ENVELOPE",
            )
        )
    return dict(raw)


def _skill_argv(
    payload: Mapping[str, Any], errors: list[ScheduleFieldError]
) -> list[str] | None:
    raw = payload.get("argv")
    if raw is None:
        return []
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        errors.append(_field("payload.argv", "INVALID", "PROVIDE_STRING_ARRAY"))
        return None
    return list(raw)


def _skill_window(
    payload: Mapping[str, Any], errors: list[ScheduleFieldError]
) -> ScheduleWindow | None:
    raw = payload.get("window")
    if raw is None:
        return ScheduleWindow()
    try:
        return ScheduleWindow.model_validate(raw)
    except ValidationError as exc:
        errors.extend(_validation_errors("payload.window", exc))
        return None


def _agent_turn(
    payload: Mapping[str, Any],
    context: ScheduleAuthoringContext,
    errors: list[ScheduleFieldError],
) -> AgentTurnPayload | None:
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        errors.append(_field("payload.prompt", "MISSING", "PROVIDE_PROMPT"))
        return None
    budget_ref = payload.get("budget_ref") or "platform"
    if budget_ref != "platform":
        errors.append(
            _field(
                "payload.budget_ref",
                "UNSUPPORTED_BUDGET_SOURCE",
                "USE_PLATFORM_BUDGET",
                ("platform",),
            )
        )
    tools = _tools(payload.get("tools_allow"), context, errors)
    model_ref = payload.get("model_ref")
    if model_ref is not None and (not isinstance(model_ref, str) or not model_ref.strip()):
        errors.append(_field("payload.model_ref", "INVALID", "PROVIDE_MODEL_REFERENCE"))
    if tools is None or errors:
        return None
    try:
        return AgentTurnPayload(
            prompt=prompt.strip(),
            model_ref=model_ref.strip() if isinstance(model_ref, str) else None,
            tools_allow=tools,
            budget_ref=budget_ref,
        )
    except ValidationError as exc:
        errors.extend(_validation_errors("payload", exc))
        return None


def _tools(
    raw: Any,
    context: ScheduleAuthoringContext,
    errors: list[ScheduleFieldError],
) -> list[str] | None:
    if raw is None:
        return []
    if not isinstance(raw, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw
    ):
        errors.append(_field("payload.tools_allow", "INVALID", "PROVIDE_STRING_ARRAY"))
        return None
    entries = [item.strip() for item in raw]
    if len(set(entries)) != len(entries):
        errors.append(_field("payload.tools_allow", "DUPLICATE", "REMOVE_DUPLICATE_TOOLS"))
        return None
    if "*" in entries:
        if len(entries) > 1:
            errors.append(
                _field("payload.tools_allow", "WILDCARD_MIXED", "USE_WILDCARD_ALONE")
            )
            return None
        return ["*"]
    compiled: list[str] = []
    for entry in entries:
        resolved = _tool_entry(entry, context, errors)
        if resolved is not None:
            compiled.append(resolved)
    return compiled if len(compiled) == len(entries) else None


def _tool_entry(
    entry: str, context: ScheduleAuthoringContext, errors: list[ScheduleFieldError]
) -> str | None:
    parts = entry.split(":", 2)
    if parts[0] == "SKILL":
        return _skill_tool_entry(parts, context, errors)
    if parts[0] == "MCP":
        return _mcp_tool_entry(entry, parts, context, errors)
    if parts[0] == "PLATFORM":
        return _platform_tool_entry(entry, parts, errors)
    errors.append(_invalid_tool_entry())
    return None


def _skill_tool_entry(
    parts: list[str], context: ScheduleAuthoringContext, errors: list[ScheduleFieldError]
) -> str | None:
    if len(parts) != 3 or not parts[2]:
        errors.append(_invalid_tool_entry())
        return None
    skill = _owned_skill(parts[1], context)
    if skill is None:
        errors.append(
            _field(
                "payload.tools_allow",
                "SKILL_NOT_OWNED",
                "CHOOSE_EFFECTIVE_SKILL",
                tuple(sorted(context.skills)),
            )
        )
        return None
    names = _declared_skill_tools(skill)
    if names and parts[2] not in names:
        errors.append(
            _field(
                "payload.tools_allow",
                "TOOL_NOT_OWNED",
                "CHOOSE_DECLARED_SKILL_TOOL",
                tuple(sorted(names)),
            )
        )
        return None
    return f"SKILL:{skill.skill_id}:{parts[2]}"


def _mcp_tool_entry(
    entry: str,
    parts: list[str],
    context: ScheduleAuthoringContext,
    errors: list[ScheduleFieldError],
) -> str | None:
    if len(parts) != 3 or not parts[1] or not parts[2]:
        errors.append(_invalid_tool_entry())
        return None
    server = next((item for item in context.mcp_servers if item.key == parts[1]), None)
    if server is None:
        errors.append(
            _field(
                "payload.tools_allow",
                "MCP_SERVER_NOT_OWNED",
                "CHOOSE_OWNED_MCP_SERVER",
                tuple(sorted(item.key for item in context.mcp_servers)),
            )
        )
        return None
    names = {
        str(item.get("name"))
        for item in server.definitions
        if isinstance(item, Mapping) and item.get("name")
    }
    if names and parts[2] not in names:
        errors.append(
            _field(
                "payload.tools_allow",
                "TOOL_NOT_OWNED",
                "CHOOSE_DECLARED_MCP_TOOL",
                tuple(sorted(names)),
            )
        )
        return None
    return entry


def _platform_tool_entry(
    entry: str, parts: list[str], errors: list[ScheduleFieldError]
) -> str | None:
    if len(parts) != 2 or not parts[1]:
        errors.append(_invalid_tool_entry())
        return None
    if parts[1] not in _platform_tools:
        errors.append(
            _field(
                "payload.tools_allow",
                "PLATFORM_TOOL_UNKNOWN",
                "CHOOSE_PLATFORM_TOOL",
                tuple(sorted(_platform_tools)),
            )
        )
        return None
    return entry


def _invalid_tool_entry() -> ScheduleFieldError:
    return _field(
        "payload.tools_allow",
        "INVALID_TOOL_ENTRY",
        "USE_TOOL_SYNTAX",
        ("*", "PLATFORM:<tool>", "MCP:<server>:<tool>", "SKILL:<skill>:<tool>"),
    )


def _owned_skill(
    key_or_id: str, context: ScheduleAuthoringContext
) -> ResolvedSkill | None:
    by_key = context.skills.get(key_or_id)
    if by_key is not None:
        return by_key
    try:
        skill_id = uuid.UUID(key_or_id)
    except ValueError:
        return None
    return next(
        (item for item in context.skills.values() if item.skill_id == skill_id),
        None,
    )


def _declared_skill_tools(skill: ResolvedSkill) -> set[str]:
    frontmatter = skill.frontmatter if isinstance(skill.frontmatter, Mapping) else {}
    declared = frontmatter.get("tools")
    if not isinstance(declared, list):
        return set()
    names: set[str] = set()
    for item in declared:
        if isinstance(item, str) and item:
            names.add(item)
        elif isinstance(item, Mapping) and isinstance(item.get("name"), str):
            names.add(str(item["name"]))
    return names


def _delivery(
    arguments: Mapping[str, Any],
    context: ScheduleAuthoringContext,
    errors: list[ScheduleFieldError],
) -> DeliveryPlan | None:
    raw = arguments.get("delivery")
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        errors.append(_field("delivery", "INVALID", "PROVIDE_DELIVERY_OBJECT"))
        return None
    requested = raw.get("intent")
    if requested is not None and requested not in ("NONE", "PUSH"):
        errors.append(
            _field("delivery.intent", "INVALID", "CHOOSE_DELIVERY_INTENT", ("NONE", "PUSH"))
        )
        return None
    intent = requested or _default_intent(context, errors)
    if intent is None:
        return None
    if intent == "NONE":
        if raw.get("route") is not None:
            errors.append(
                _field("delivery.route", "ROUTE_NOT_ALLOWED", "REMOVE_ROUTE")
            )
            return None
        return DeliveryPlan(intent=DeliveryIntent.NONE)
    route = _push_route(raw, context, errors)
    if route is None:
        return None
    return DeliveryPlan(intent=DeliveryIntent.PUSH, route=route)


def _default_intent(
    context: ScheduleAuthoringContext, errors: list[ScheduleFieldError]
) -> str | None:
    if context.entry_channel is None:
        return "NONE"
    if context.entry_channel in OUTBOUND_CHANNELS:
        return "PUSH"
    if context.entry_channel in LOCAL_STREAM_CHANNELS:
        return "NONE"
    errors.append(
        _field(
            "delivery.intent",
            "ENTRY_CHANNEL_UNSUPPORTED",
            "CHOOSE_DELIVERY_INTENT",
            ("NONE", "PUSH"),
        )
    )
    return None


def _push_route(
    raw: Mapping[str, Any],
    context: ScheduleAuthoringContext,
    errors: list[ScheduleFieldError],
) -> DeliveryRouteInput | None:
    requested_target = raw.get("target")
    if requested_target is not None:
        if not isinstance(requested_target, str) or not requested_target.strip():
            errors.append(_field("delivery.target", "INVALID", "CHOOSE_DELIVERY_TARGET"))
            return None
        wanted = requested_target.strip()
        matches = [item for item in context.delivery_targets if item.label == wanted]
        if not matches:
            # 单通道单目标时允许模型传通道名（用户只会说“企微”）；多个则必须给 label。
            matches = [
                item for item in context.delivery_targets if item.route.channel == wanted
            ]
        if len(matches) != 1:
            reason = "DELIVERY_TARGET_AMBIGUOUS" if matches else "DELIVERY_TARGET_NOT_FOUND"
            allowed = tuple(sorted({item.label for item in context.delivery_targets}))
            errors.append(
                _field("delivery.target", reason, "LIST_DELIVERY_TARGETS", allowed)
            )
            return None
        if matches[0].route.channel not in OUTBOUND_CHANNELS:
            errors.append(
                _field(
                    "delivery.target",
                    "DELIVERY_CHANNEL_UNSUPPORTED",
                    "CHOOSE_OUTBOUND_CHANNEL",
                    tuple(sorted(OUTBOUND_CHANNELS)),
                )
            )
            return None
        return matches[0].route
    supplied = raw.get("route")
    route: DeliveryRouteInput | None
    if supplied is not None:
        if not isinstance(supplied, Mapping):
            errors.append(_field("delivery.route", "INVALID", "PROVIDE_ROUTE_OBJECT"))
            return None
        try:
            route = DeliveryRouteInput.model_validate(supplied)
        except ValidationError as exc:
            errors.extend(_validation_errors("delivery.route", exc))
            return None
        if context.delivery_route is None or route != context.delivery_route:
            errors.append(
                _field("delivery.route", "ROUTE_NOT_OWNED", "USE_SOURCE_CHAT_ROUTE")
            )
            return None
    else:
        route = context.delivery_route
        if route is None:
            errors.append(
                _field(
                    "delivery.route",
                    "DELIVERY_ROUTE_UNAVAILABLE",
                    "BIND_OUTBOUND_CHANNEL_OR_CHOOSE_WEB_ONLY",
                )
            )
            return None
    if route.channel not in OUTBOUND_CHANNELS:
        errors.append(
            _field(
                "delivery.route",
                "DELIVERY_CHANNEL_UNSUPPORTED",
                "CHOOSE_OUTBOUND_CHANNEL",
                tuple(sorted(OUTBOUND_CHANNELS)),
            )
        )
        return None
    return route


def _session(
    arguments: Mapping[str, Any],
    context: ScheduleAuthoringContext,
    errors: list[ScheduleFieldError],
) -> SessionTarget | None:
    raw = arguments.get("session_target")
    if raw is None:
        raw = "isolated"
    try:
        target = SessionTarget.model_validate(raw)
    except ValidationError as exc:
        errors.extend(_validation_errors("session_target", exc))
        return None
    if target.kind in ("current", "main") and context.conversation_id is None:
        errors.append(
            _field(
                "session_target",
                "SESSION_CONTEXT_UNAVAILABLE",
                "USE_ISOLATED_OR_BIND_CHAT",
            )
        )
        return None
    return target


def _reason(
    arguments: Mapping[str, Any],
    plan: SchedulePlan,
    errors: list[ScheduleFieldError],
) -> str:
    raw = arguments.get("reason")
    if raw is not None:
        if not isinstance(raw, str) or not raw.strip():
            errors.append(_field("reason", "INVALID", "PROVIDE_REASON"))
            return ""
        if len(raw.strip()) > 2000:
            errors.append(_field("reason", "TOO_LONG", "SHORTEN_REASON"))
            return ""
        return raw.strip()
    payload = plan.payload
    if isinstance(payload, ReminderPayload):
        return "Fixed notification; no model or tools are needed."
    if isinstance(payload, SkillPlanPayload):
        return "Fixed Skill invocation with frozen parameters; no model is needed."
    return "Runtime judgment over the owner tool surface; the platform budget applies."


def _budget_source(plan: SchedulePlan) -> str:
    return "PLATFORM" if isinstance(plan.payload, AgentTurnPayload) else "NONE"


def _clarification_result(
    kind: str | None,
    confidence: float,
    timezone: str | None,
    recurrence: dict[str, Any] | None,
    *,
    plan: SchedulePlan | None = None,
    reason: str = "",
) -> ScheduleAuthoringResult:
    question = _business_question(kind)
    clarification = ScheduleClarification(
        question=question,
        reason=reason or "The business intent is not specific enough to compile one plan.",
        fields=("payload",) if kind is None else (),
        confidence=confidence,
    )
    return ScheduleAuthoringResult(
        plan=plan,
        confidence=confidence,
        reason=clarification.reason,
        budget_source=_budget_source(plan) if plan is not None else "NONE",
        timezone=timezone,
        recurrence=recurrence,
        clarification=clarification,
    )


def _business_question(kind: str | None) -> str:
    if kind == "REMINDER":
        return "这条提醒的固定内容和触发时间都已经确定了吗？"
    if kind == "SKILL_PLAN":
        return "这个任务每次处理的客户和参数都是固定的，还是需要运行时判断？"
    if kind == "AGENT_TURN":
        return "这个任务每次需要自己判断哪些内容，还是只处理一个固定对象？"
    return "为了把任务建准确：每次处理的对象和范围是固定的，还是需要根据当时的数据判断？"


def _reject_unknown_variables(
    value: Any, field: str, errors: list[ScheduleFieldError]
) -> None:
    unknown: set[str] = set()
    for text in _iter_strings(value):
        unknown.update(
            name for name in _variable_pattern.findall(text) if name not in _window_variables
        )
    if unknown:
        errors.append(
            _field(
                field,
                "UNKNOWN_WINDOW_VARIABLE",
                "USE_ALLOWED_WINDOW_VARIABLES",
                tuple(sorted(_window_variables)),
            )
        )


def _iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def _validation_errors(prefix: str, exc: ValidationError) -> list[ScheduleFieldError]:
    errors: list[ScheduleFieldError] = []
    for item in exc.errors():
        location = ".".join(str(part) for part in item.get("loc") or ())
        field = f"{prefix}.{location}" if location else prefix
        reason = "MISSING" if item.get("type") == "missing" else "INVALID"
        errors.append(_field(field, reason, "FIX_FIELD"))
    return errors


def _failure(errors: Sequence[ScheduleFieldError]) -> ScheduleAuthoringError:
    reasons = {item.reason for item in errors}
    if "TIMEZONE_MISSING" in reasons:
        code = ErrorCode.SCHEDULE_TIMEZONE_REQUIRED.value
        message = (
            "the user's IANA time zone is required; ask the user instead of assuming the "
            "server time zone"
        )
    elif reasons.intersection(_delivery_reasons):
        code = ErrorCode.SCHEDULE_DELIVERY_UNSUPPORTED.value
        message = "the delivery choice cannot be satisfied by this surface"
    elif any(item.reason == "MISSING" for item in errors):
        code = ErrorCode.SCHEDULE_PARAMETER_INCOMPLETE.value
        message = "missing required schedule fields: " + ", ".join(
            item.field for item in errors if item.reason == "MISSING"
        )
    else:
        code = ErrorCode.SCHEDULE_DRAFT_INVALID.value
        message = "invalid schedule proposal: " + ", ".join(
            f"{item.field} ({item.reason})" for item in errors
        )
    return ScheduleAuthoringError(code, message, errors)


def _field(
    field: str,
    reason: str,
    repair: str,
    allowed_values: Sequence[str] = (),
) -> ScheduleFieldError:
    return ScheduleFieldError(
        field=field,
        reason=reason,
        repair=repair,
        allowed_values=tuple(str(item) for item in allowed_values),
    )


__all__ = [
    "ScheduleAuthoringContext",
    "ScheduleAuthoringError",
    "ScheduleAuthoringResult",
    "ScheduleClarification",
    "ScheduleFieldError",
    "compile_schedule_plan",
]
