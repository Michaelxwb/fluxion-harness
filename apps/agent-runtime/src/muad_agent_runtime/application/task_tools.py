"""Runtime 内置任务工具（docs/04 §7.5，scheduled-task-v2 design §2.3.5 / §3.2.8）。

**模型侧只有一条写路径：产 Draft。** `draft_schedule` / `draft_schedule_update` 把候选配置交给
Worker 的 Draft API（API-01），回执只有 `draft_id / draft_hash / preview / expires_at`；同一
回合 Runtime 再写一条 canonical `schedule.draft.ready`，聊天流据此渲染确认卡片。生效只能由
用户在 surface 上显式确认（API-31 → API-27）——这里没有确认端点，也没有「同轮自确认」的工具；
任何把 Draft 当创建用的调用在网络协议层就不可达（`SCHEDULE_CONFIRMATION_REQUIRED`）。

**查询与生命周期**走已授权的 Worker 生命周期 API。目标用 `schedule_id` 精确定位，或用名字/
目标描述匹配：命中多条一律 `SCHEDULE_AMBIGUOUS` 并回候选，绝不静默挑一个；零命中是
`SCHEDULE_NOT_FOUND`。`ENDED`/`CANCELLED` 的任务不可改也不可恢复——只能新建。

**自然语言时间**由模型经 `current_time` 推算，但时区必须是用户确认过的 IANA 名：缺时区就问
用户（`SCHEDULE_TIMEZONE_REQUIRED`），绝不回退服务端时区。多个客户/独立目标必须各建各的
Schedule：参数里出现多值目标即拒绝并提示拆分（design §2.3.9）。

actor 只取自已认证 Run 上下文（工具参数里没有 actor 字段，无法伪造），Worker 端再按
`X-Actor-User-Id` 校验归属。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from muad_agent_core.tools import ToolConcurrency, ToolDefinition, ToolEffect, ToolHandler, ToolRegistry
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    DeliveryIntent,
    DraftKind,
    ResolvedSkill,
    SchedulePreview,
)
from muad_contracts.enums import LOCAL_STREAM_CHANNELS, OUTBOUND_CHANNELS, ScheduleStatus
from muad_contracts.webchat import WebchatScheduleDraftReady
from pydantic import ValidationError

from ..infrastructure.db import SessionFactoryProvider
from .schedule_draft_events import publish_schedule_draft_ready
from .task_client import (
    TaskSubmissionContext,
    WorkerTaskClient,
    submission_idempotency_key,
)
from .time_tools import resolve_zone

logger = logging.getLogger(__name__)

DRAFT_SCHEDULE_TOOL = "draft_schedule"
DRAFT_SCHEDULE_UPDATE_TOOL = "draft_schedule_update"
LIST_SCHEDULES_TOOL = "list_schedules"
GET_SCHEDULE_TOOL = "get_schedule"
PAUSE_SCHEDULE_TOOL = "pause_schedule"
RESUME_SCHEDULE_TOOL = "resume_schedule"
CANCEL_SCHEDULE_TOOL = "cancel_schedule"
GET_TASK_TOOL = "get_task"
LIST_TASKS_TOOL = "list_tasks"
CANCEL_TASK_TOOL = "cancel_task"

#: 终态任务：只能新建，不能改时间也不能恢复（design §3.2.10 / E-13）。
TERMINAL_SCHEDULE_STATUSES = frozenset({ScheduleStatus.ENDED.value, ScheduleStatus.CANCELLED.value})

TASK_SUMMARY_KEYS = (
    "task_id",
    "status",
    "intent_key",
    "trigger_type",
    "schedule_id",
    "cancel_requested",
    "result",
    "error_code",
    "error_message",
    "delivery_status",
    "deadline_at",
    "create_time",
    "finished_at",
)

_STRING: Mapping[str, Any] = {"type": "string"}
_OBJECT: Mapping[str, Any] = {"type": "object"}
_PAGE: Mapping[str, Any] = {"type": "integer", "minimum": 1}
_RECURRENCE: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["CRON", "ONCE"]},
        "cron": {**_STRING, "description": "5-field cron, interpreted in the schedule timezone"},
        "run_at": {**_STRING, "description": "ISO8601 with offset (ONCE only)"},
    },
    "required": ["kind"],
    "additionalProperties": False,
}
_ALLOWLIST_ENTRY: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "source": {"type": "string", "enum": ["MCP", "SKILL"]},
        "tool_name": _STRING,
        # SKILL 用本 Run 的有效 skill key（Run 里没有 UUID 目录）；MCP 用 server_key。
        "skill_key": _STRING,
        "server_key": _STRING,
    },
    "required": ["source", "tool_name"],
    "additionalProperties": False,
}
_DRAFT_ARGS: Mapping[str, Any] = {
    "instruction": {
        **_STRING,
        "description": "The user's original request, verbatim (max 4000 chars).",
    },
    "mode": {"type": "string", "enum": ["REMINDER", "SKILL", "AGENTIC"]},
    "timezone": {
        **_STRING,
        "description": "IANA time zone confirmed by the user, e.g. Asia/Shanghai. Never guess.",
    },
    "recurrence": _RECURRENCE,
    "name": _STRING,
    "reminder_text": {**_STRING, "description": "REMINDER only: the fixed text to deliver."},
    "skill_key": {**_STRING, "description": "SKILL only: effective skill key of this run."},
    "input_template": {
        "type": "object",
        "description": "SKILL only. May use {{fire_date}}, {{previous_day}}, {{fire_time}}.",
    },
    "goal": {**_STRING, "description": "AGENTIC only: the business goal."},
    "tool_allowlist": {
        "type": "array",
        "items": _ALLOWLIST_ENTRY,
        "description": "AGENTIC only: explicit tools (non-empty). No wildcard tools.",
    },
    "budget": {
        "type": "object",
        "properties": {
            "max_steps": {"type": "integer", "minimum": 1},
            "max_tool_calls": {"type": "integer", "minimum": 1},
            "max_tokens": {"type": "integer", "minimum": 0},
        },
        "required": ["max_steps", "max_tool_calls"],
        "additionalProperties": False,
    },
    "context": {
        "type": "object",
        "properties": {"parameters": _OBJECT, "output": _OBJECT},
        "additionalProperties": False,
    },
}
#: 各模式专属字段：出现别的模式的专属字段就是模型理解错了，显式拒绝而不是静默丢。
MODE_ONLY_FIELDS = {
    "REMINDER": ("reminder_text",),
    "SKILL": ("skill_key", "input_template"),
    "AGENTIC": ("goal", "tool_allowlist", "budget"),
}


class TaskToolError(RuntimeError):
    """工具面的稳定失败：`code` 进模型上下文，`data` 只放可公开的结构化提示。"""

    def __init__(self, code: str, message: str, data: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


def _schema(properties: Mapping[str, Any], required: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def _error(code: str, message: str, data: Mapping[str, Any] | None = None) -> str:
    body: dict[str, Any] = {"code": code, "message": message}
    if data:
        body["data"] = dict(data)
    return json.dumps({"error": body}, ensure_ascii=False)


def _uuid_arg(arguments: Mapping[str, Any], key: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(arguments.get(key)))
    except ValueError as exc:
        raise TaskToolError(ErrorCode.COMMON_VALIDATION_ERROR.value, f"{key} must be a UUID") from exc


def _object_arg(arguments: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TaskToolError(ErrorCode.COMMON_VALIDATION_ERROR.value, f"{key} must be an object")
    return dict(value)


def _query(arguments: Mapping[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if isinstance(arguments.get("status"), str):
        params["status"] = arguments["status"]
    page = arguments.get("page")
    params["page"] = page if isinstance(page, int) and page >= 1 else 1
    return params


def _task_summary(data: Mapping[str, Any]) -> dict[str, Any]:
    return {key: data.get(key) for key in TASK_SUMMARY_KEYS}


Handler = Callable[..., Awaitable[Any]]
ResourceKey = Callable[[Mapping[str, Any]], str | None]
#: name / description / schema / effect / handler / concurrency / resource_key
ToolSpec = tuple[str, str, dict[str, Any], ToolEffect, Handler, ToolConcurrency, ResourceKey | None]


def _task_resource_key(arguments: Mapping[str, Any]) -> str | None:
    task_id = arguments.get("task_id")
    return f"task:{task_id}" if isinstance(task_id, str) and task_id else None


def _schedule_resource_key(arguments: Mapping[str, Any]) -> str | None:
    schedule_id = arguments.get("schedule_id")
    return f"schedule:{schedule_id}" if isinstance(schedule_id, str) and schedule_id else None


def _task_list_resource_key(arguments: Mapping[str, Any]) -> str:
    """任务列表读取锁在「本用户的任务列表」这一资源上（同键串行）。"""
    return "tasks:list"


def _schedule_list_resource_key(arguments: Mapping[str, Any]) -> str:
    """按名字/目标定位要读全量列表：锁在「本用户的 Schedule 列表」上避免并发错位。"""
    return "schedules:list"


def _timezone_arg(arguments: Mapping[str, Any]) -> str:
    """Draft 必须有用户确认过的 IANA 时区；缺就问用户，不退服务端默认时区（E-01）。"""
    value = arguments.get("timezone")
    if not isinstance(value, str) or not value.strip():
        raise TaskToolError(
            ErrorCode.SCHEDULE_TIMEZONE_REQUIRED.value,
            "the user's IANA time zone is required; ask the user instead of assuming the "
            "server time zone (current_time shows the conversation's configured zone)",
        )
    try:
        return resolve_zone(value.strip()).key
    except ValueError as exc:
        raise TaskToolError(
            ErrorCode.SCHEDULE_DRAFT_INVALID.value,
            "timezone must be a valid IANA name, e.g. Asia/Shanghai",
            {"reason": "INVALID_TIMEZONE"},
        ) from exc


def _instruction_arg(arguments: Mapping[str, Any]) -> str:
    value = arguments.get("instruction")
    if not isinstance(value, str) or not value.strip():
        raise TaskToolError(
            ErrorCode.COMMON_VALIDATION_ERROR.value, "instruction is required"
        )
    return value.strip()


def _delivery_intent(entry_channel: str | None) -> str:
    """投递能力只由入口 surface 决定（共享通道词表判能力，核心域不点名渠道）。

    Runtime 先算好再发：Worker 会核对 body 的 delivery_intent 与入口能力一致，
    两边都用同一份共享词表，因此不会出现「核心域自己编一个投递承诺」。
    """
    if entry_channel is None:
        return DeliveryIntent.NONE.value
    if entry_channel in OUTBOUND_CHANNELS:
        return DeliveryIntent.PUSH.value
    if entry_channel in LOCAL_STREAM_CHANNELS:
        return DeliveryIntent.NONE.value
    raise TaskToolError(
        ErrorCode.SCHEDULE_DELIVERY_UNSUPPORTED.value,
        "this entry surface has no delivery capability registered",
        {"reason": "ENTRY_CHANNEL_UNSUPPORTED"},
    )


def _multi_target_fields(parameters: Mapping[str, Any]) -> list[str]:
    """一个 Draft 只承载一个目标：参数里出现多值列表就是被合并的复合指令（design §2.3.9）。"""
    return sorted(
        str(key)
        for key, value in parameters.items()
        if isinstance(value, (list, tuple)) and len({json.dumps(item, sort_keys=True) for item in value}) > 1
    )


def _context_arg(arguments: Mapping[str, Any]) -> dict[str, Any]:
    raw = _object_arg(arguments, "context") or {}
    parameters = raw.get("parameters")
    if parameters is not None and not isinstance(parameters, Mapping):
        raise TaskToolError(ErrorCode.COMMON_VALIDATION_ERROR.value, "context.parameters must be an object")
    merged: dict[str, Any] = {}
    if isinstance(parameters, Mapping):
        merged = _multi_target_guard(parameters)
    output = raw.get("output")
    if output is not None and not isinstance(output, Mapping):
        raise TaskToolError(ErrorCode.COMMON_VALIDATION_ERROR.value, "context.output must be an object")
    return {"parameters": merged, "output": dict(output) if isinstance(output, Mapping) else {}}


def _multi_target_guard(parameters: Mapping[str, Any]) -> dict[str, Any]:
    collapsed = _multi_target_fields(parameters)
    if collapsed:
        raise TaskToolError(
            ErrorCode.SCHEDULE_DRAFT_INVALID.value,
            "one draft carries exactly one target: split the request and create one schedule "
            "per customer/target, each with a single value for these parameters",
            {"reason": "MULTIPLE_TARGETS", "fields": collapsed},
        )
    return dict(parameters)


def _recurrence_arg(arguments: Mapping[str, Any], timezone: str) -> dict[str, Any]:
    raw = _object_arg(arguments, "recurrence")
    if raw is None:
        raise TaskToolError(ErrorCode.COMMON_VALIDATION_ERROR.value, "recurrence is required")
    recurrence = {**raw, "timezone": timezone}
    kind = recurrence.get("kind")
    if kind == "CRON" and not isinstance(recurrence.get("cron"), str):
        raise TaskToolError(
            ErrorCode.SCHEDULE_PARAMETER_INCOMPLETE.value, "recurrence.cron is required for CRON",
            {"fields": ["recurrence.cron"]},
        )
    if kind == "ONCE" and not isinstance(recurrence.get("run_at"), str):
        raise TaskToolError(
            ErrorCode.SCHEDULE_PARAMETER_INCOMPLETE.value, "recurrence.run_at is required for ONCE",
            {"fields": ["recurrence.run_at"]},
        )
    return recurrence


def _execution_spec(
    arguments: Mapping[str, Any], mode: str, skills: Mapping[str, ResolvedSkill]
) -> dict[str, Any]:
    """按模式组装执行规格：Skill 只认本 Run 的有效 key，AGENTIC 必须显式非空 allowlist。"""
    for other, fields in MODE_ONLY_FIELDS.items():
        if other == mode:
            continue
        present = [name for name in fields if arguments.get(name) is not None]
        if present:
            raise TaskToolError(
                ErrorCode.COMMON_VALIDATION_ERROR.value,
                f"{', '.join(present)} only apply to mode {other}",
            )
    if mode == "REMINDER":
        text = arguments.get("reminder_text")
        if not isinstance(text, str) or not text.strip():
            raise TaskToolError(
                ErrorCode.SCHEDULE_PARAMETER_INCOMPLETE.value,
                "reminder_text is required for REMINDER",
                {"fields": ["reminder_text"]},
            )
        return {"mode": "REMINDER", "reminder_text": text.strip()}
    if mode == "SKILL":
        return {
            "mode": "SKILL",
            "skill_id": str(_skill(arguments, skills).skill_id),
            "input_template": _object_arg(arguments, "input_template") or {},
        }
    return _agentic_spec(arguments, skills)


def _skill(arguments: Mapping[str, Any], skills: Mapping[str, ResolvedSkill]) -> ResolvedSkill:
    key = arguments.get("skill_key")
    skill = skills.get(key.strip()) if isinstance(key, str) else None
    if skill is None:
        raise TaskToolError("SKILL_NOT_EFFECTIVE", f"skill is not effective for this run: {key}")
    return skill


def _agentic_spec(arguments: Mapping[str, Any], skills: Mapping[str, ResolvedSkill]) -> dict[str, Any]:
    goal = arguments.get("goal")
    allowlist = arguments.get("tool_allowlist")
    budget = _object_arg(arguments, "budget")
    if not isinstance(goal, str) or not goal.strip():
        raise TaskToolError(
            ErrorCode.SCHEDULE_PARAMETER_INCOMPLETE.value,
            "goal is required for AGENTIC",
            {"fields": ["goal"]},
        )
    if not isinstance(allowlist, list) or not allowlist:
        raise TaskToolError(
            ErrorCode.SCHEDULE_PARAMETER_INCOMPLETE.value,
            "tool_allowlist must be a non-empty list for AGENTIC; arbitrary tools are not allowed",
            {"fields": ["execution_spec.tool_allowlist"]},
        )
    if not budget or budget.get("max_steps") is None or budget.get("max_tool_calls") is None:
        raise TaskToolError(
            ErrorCode.SCHEDULE_PARAMETER_INCOMPLETE.value,
            "budget.max_steps and budget.max_tool_calls are required for AGENTIC",
            {"fields": ["budget"]},
        )
    return {
        "mode": "AGENTIC",
        "goal": goal.strip(),
        "tool_allowlist": [_allowlist_entry(item, skills) for item in allowlist],
        "budget": {
            "max_steps": budget["max_steps"],
            "max_tool_calls": budget["max_tool_calls"],
            "max_tokens": budget.get("max_tokens", 0),
        },
    }


def _allowlist_entry(item: Any, skills: Mapping[str, ResolvedSkill]) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise TaskToolError(ErrorCode.COMMON_VALIDATION_ERROR.value, "tool_allowlist entries must be objects")
    source = item.get("source")
    tool_name = item.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise TaskToolError(ErrorCode.COMMON_VALIDATION_ERROR.value, "tool_allowlist.tool_name is required")
    if source == "SKILL":
        return {
            "source": "SKILL",
            "tool_name": tool_name.strip(),
            "skill_id": str(_skill(item, skills).skill_id),
        }
    if source == "MCP":
        server_key = item.get("server_key")
        if not isinstance(server_key, str) or not server_key.strip():
            raise TaskToolError(
                ErrorCode.COMMON_VALIDATION_ERROR.value, "MCP allowlist entries need server_key"
            )
        return {"source": "MCP", "tool_name": tool_name.strip(), "server_key": server_key.strip()}
    raise TaskToolError(ErrorCode.COMMON_VALIDATION_ERROR.value, "tool_allowlist.source must be MCP or SKILL")


class BackgroundTaskToolSet:
    """注册任务/定时任务工具。`session_factory` 只用于同回合写 Draft 确认卡片事件。"""

    def __init__(
        self,
        *,
        client: WorkerTaskClient,
        context: TaskSubmissionContext,
        skills: Sequence[ResolvedSkill],
        session_factory: SessionFactoryProvider | None = None,
    ) -> None:
        self._client = client
        self._context = context
        self._skills = {skill.key: skill for skill in skills}
        self._session_factory = session_factory

    def register(self, registry: ToolRegistry) -> None:
        for definition in self._definitions():
            registry.register(definition)

    def _wrap(self, handler: Handler) -> ToolHandler:
        async def run(arguments: Mapping[str, Any], *, call_id: str) -> str:
            try:
                return json.dumps(await handler(arguments, call_id=call_id), ensure_ascii=False, default=str)
            except TaskToolError as exc:
                return _error(exc.code, exc.message, exc.data)
            except AppError as exc:
                return _error(str(exc.code), str(exc.code))

        return run

    def _definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            ToolDefinition(
                name=name,
                description=description,
                input_schema=schema,
                effect=effect,
                handler=self._wrap(handler),
                concurrency=concurrency,
                resource_key=resource_key,
            )
            for name, description, schema, effect, handler, concurrency, resource_key in (
                *self._schedule_specs(),
                *self._task_specs(),
            )
        )

    def _schedule_specs(self) -> tuple[ToolSpec, ...]:
        draft_schema = _schema(_DRAFT_ARGS, ("instruction", "mode", "timezone", "recurrence"))
        update_schema = _schema(
            {**_DRAFT_ARGS, "schedule_id": _STRING, "query": _STRING},
            (
                "instruction",
                "mode",
                "timezone",
                "recurrence",
                "schedule_id|query",
            ),
        )
        update_schema["required"] = ["instruction", "mode", "timezone", "recurrence"]
        return (
            (
                DRAFT_SCHEDULE_TOOL,
                "Prepare a NEW scheduled task for the user to confirm. It only creates a draft: "
                "nothing runs and nothing is saved until the user confirms the card in the chat. "
                "Pick mode REMINDER for fixed text, SKILL when the skill and inputs are fixed, "
                "AGENTIC for a natural-language goal over explicit tools. Call it once per "
                "independent customer/target. Ask the user for their IANA time zone when unknown.",
                draft_schema,
                ToolEffect.WRITE,
                self._draft_schedule,
                ToolConcurrency.SERIAL,
                None,
            ),
            (
                DRAFT_SCHEDULE_UPDATE_TOOL,
                "Prepare a CHANGE to one of the user's schedules (same fields as "
                f"{DRAFT_SCHEDULE_TOOL}, plus schedule_id or query to locate it). Repeat the "
                "COMPLETE new configuration, including parts that do not change; the preview "
                "shows the before/after diff. It only creates a draft — the user must confirm it.",
                update_schema,
                ToolEffect.WRITE,
                self._draft_schedule_update,
                ToolConcurrency.SERIAL,
                None,
            ),
            (
                LIST_SCHEDULES_TOOL,
                "List the current user's schedules, newest first.",
                _schema({"status": _STRING, "page": _PAGE}, ()),
                ToolEffect.READ,
                self._list_schedules,
                ToolConcurrency.PARALLEL_READ,
                _schedule_list_resource_key,
            ),
            (
                GET_SCHEDULE_TOOL,
                "Get one of the user's schedules by schedule_id, or by name/target text. "
                "Ambiguous text returns the candidates instead of guessing.",
                _schema({"schedule_id": _STRING, "query": _STRING}, ()),
                ToolEffect.READ,
                self._get_schedule,
                ToolConcurrency.PARALLEL_READ,
                _schedule_resource_key,
            ),
            (
                PAUSE_SCHEDULE_TOOL,
                "Pause one of the user's schedules: stop future fires, keep running executions.",
                _schema({"schedule_id": _STRING, "query": _STRING, "reason": _STRING}, ()),
                ToolEffect.WRITE,
                self._pause_schedule,
                ToolConcurrency.SERIAL,
                None,
            ),
            (
                RESUME_SCHEDULE_TOOL,
                "Resume a paused schedule from now on; misses during the pause are not replayed.",
                _schema({"schedule_id": _STRING, "query": _STRING, "reason": _STRING}, ()),
                ToolEffect.WRITE,
                self._resume_schedule,
                ToolConcurrency.SERIAL,
                None,
            ),
            (
                CANCEL_SCHEDULE_TOOL,
                "Cancel one of the user's schedules. scope FUTURE_ONLY (default) keeps running "
                "executions; ALL_ACTIVE also ends them. A finished (ENDED) schedule cannot be "
                "restarted — create a new one instead.",
                _schema(
                    {
                        "schedule_id": _STRING,
                        "query": _STRING,
                        "scope": {"type": "string", "enum": ["FUTURE_ONLY", "ALL_ACTIVE"]},
                        "reason": _STRING,
                    },
                    (),
                ),
                ToolEffect.WRITE,
                self._cancel_schedule,
                ToolConcurrency.SERIAL,
                None,
            ),
        )

    def _task_specs(self) -> tuple[ToolSpec, ...]:
        return (
            (
                GET_TASK_TOOL,
                "Get status and result of one of the user's background tasks.",
                _schema({"task_id": _STRING}, ("task_id",)),
                ToolEffect.READ,
                self._get_task,
                ToolConcurrency.PARALLEL_READ,
                _task_resource_key,
            ),
            (
                LIST_TASKS_TOOL,
                "List the current user's background tasks, newest first.",
                _schema({"status": _STRING, "page": _PAGE}, ()),
                ToolEffect.READ,
                self._list_tasks,
                ToolConcurrency.PARALLEL_READ,
                _task_list_resource_key,
            ),
            (
                CANCEL_TASK_TOOL,
                "Cancel one of the user's background tasks that has not finished yet.",
                _schema({"task_id": _STRING}, ("task_id",)),
                ToolEffect.WRITE,
                self._cancel_task,
                ToolConcurrency.SERIAL,
                None,
            ),
        )

    # ---------------------------------------------------------------- Draft

    async def _draft_schedule(self, arguments: Mapping[str, Any], *, call_id: str) -> dict[str, Any]:
        return await self._draft(arguments, kind=DraftKind.CREATE)

    async def _draft_schedule_update(
        self, arguments: Mapping[str, Any], *, call_id: str
    ) -> dict[str, Any]:
        target = await self._resolve_schedule(arguments)
        _editable_target(target)
        return await self._draft(arguments, kind=DraftKind.UPDATE, target=target)

    async def _draft(
        self,
        arguments: Mapping[str, Any],
        *,
        kind: DraftKind,
        target: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = self._draft_body(arguments, kind=kind, target=target)
        response = await self._client.create_schedule_draft(
            tenant_id=self._context.tenant_id,
            agent_id=self._context.agent.id,
            actor_user_id=self._context.actor_user_id,
            entry_channel=self._context.entry_channel,
            draft=body,
            idempotency_key=self._draft_key(arguments),
            trace_id=self._context.trace_id,
            locale=self._context.locale,
        )
        card = _draft_card(response, kind=kind)
        seq = await self._publish_card(card)
        return {
            "draft_id": str(card.draft_id),
            "draft_hash": card.draft_hash,
            "status": response.get("status"),
            "kind": kind.value,
            "mode": card.mode.value,
            "preview": card.preview.model_dump(mode="json"),
            "expires_at": response.get("expires_at"),
            "card_event_seq": seq,
            "card": "PUBLISHED" if seq is not None else "UNPUBLISHED",
            "next_step": (
                "Present this preview to the user; it only becomes a schedule when the user "
                "confirms it in the chat card. You cannot confirm or create it yourself."
            ),
        }

    def _draft_body(
        self,
        arguments: Mapping[str, Any],
        *,
        kind: DraftKind,
        target: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        mode = arguments.get("mode")
        if mode not in MODE_ONLY_FIELDS:
            raise TaskToolError(
                ErrorCode.COMMON_VALIDATION_ERROR.value,
                "mode must be one of REMINDER, SKILL, AGENTIC",
            )
        timezone = _timezone_arg(arguments)
        body: dict[str, Any] = {
            "kind": kind.value,
            "mode": mode,
            "instruction": _instruction_arg(arguments),
            "recurrence": _recurrence_arg(arguments, timezone),
            "context": _context_arg(arguments),
            "execution_spec": _execution_spec(arguments, mode, self._skills),
            "delivery_intent": _delivery_intent(self._context.entry_channel),
        }
        name = arguments.get("name")
        if isinstance(name, str) and name.strip():
            body["name"] = name.strip()
        if kind is DraftKind.UPDATE:
            if target is None:
                raise TaskToolError(ErrorCode.COMMON_VALIDATION_ERROR.value, "update needs a target")
            body["base_schedule_id"] = target["schedule_id"]
            body["base_config_version"] = target["config_version"]
        return body

    def _draft_key(self, arguments: Mapping[str, Any]) -> str:
        """同一个 Run 里同参数的 Draft 重试复用同一条（Worker 侧 task_submission 幂等）。"""
        canonical = json.dumps(dict(arguments), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        return f"run:{self._context.source_run_id or 'ad-hoc'}:schedule-draft:{digest}"

    async def _publish_card(self, card: WebchatScheduleDraftReady) -> int | None:
        """同回合写 canonical `schedule.draft.ready`；没有事件sink 时显式降级并留痕。"""
        context = self._context
        run_id = context.source_run_id
        if self._session_factory is None or run_id is None or context.conversation_id is None:
            logger.warning(
                "schedule_draft_card_unpublished draft_id=%s reason=no_event_sink", card.draft_id
            )
            return None
        try:
            return await publish_schedule_draft_ready(
                self._session_factory,
                tenant_id=context.tenant_id,
                conversation_id=context.conversation_id,
                run_id=run_id,
                card=card,
            )
        except Exception as exc:  # noqa: BLE001 - 卡片写失败不能把已落库的 Draft 报成失败
            logger.warning(
                "schedule_draft_card_unpublished draft_id=%s error=%s",
                card.draft_id,
                type(exc).__name__,
            )
            return None

    # ------------------------------------------------------------ Lifecycle

    async def _resolve_schedule(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """`schedule_id` 精确定位；否则按名字/目标匹配，多条一律回候选（不猜）。"""
        if isinstance(arguments.get("schedule_id"), str) and arguments["schedule_id"].strip():
            return await self._client.get_schedule(
                tenant_id=self._context.tenant_id,
                schedule_id=_uuid_arg(arguments, "schedule_id"),
                actor_user_id=self._context.actor_user_id,
                trace_id=self._context.trace_id,
            )
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise TaskToolError(
                ErrorCode.COMMON_VALIDATION_ERROR.value,
                "provide schedule_id, or query with the schedule's name/target",
            )
        return await self._match_schedule(query.strip())

    async def _match_schedule(self, query: str) -> dict[str, Any]:
        data = await self._client.list_schedules(
            tenant_id=self._context.tenant_id,
            actor_user_id=self._context.actor_user_id,
            trace_id=self._context.trace_id,
            page=1,
            page_size=100,
        )
        needle = query.casefold()
        matches = [
            dict(item)
            for item in data.get("items") or []
            if isinstance(item, Mapping)
            and needle in str(item.get("name") or "").casefold()
        ]
        if not matches:
            raise TaskToolError(
                ErrorCode.SCHEDULE_NOT_FOUND.value,
                "no schedule of this user matches that name/target",
                {"query": query},
            )
        if len(matches) > 1:
            raise TaskToolError(
                ErrorCode.SCHEDULE_AMBIGUOUS.value,
                "several schedules match; ask the user which one, then call again with its "
                "schedule_id",
                {
                    "query": query,
                    "candidates": [
                        {
                            "schedule_id": item.get("schedule_id"),
                            "name": item.get("name"),
                            "status": item.get("status"),
                            "next_fire_at": item.get("next_fire_at"),
                        }
                        for item in matches
                    ],
                },
            )
        return matches[0]

    async def _get_schedule(self, arguments: Mapping[str, Any], *, call_id: str) -> dict[str, Any]:
        return await self._resolve_schedule(arguments)

    async def _pause_schedule(self, arguments: Mapping[str, Any], *, call_id: str) -> dict[str, Any]:
        return await self._lifecycle(arguments, call_id=call_id, action="pause")

    async def _resume_schedule(self, arguments: Mapping[str, Any], *, call_id: str) -> dict[str, Any]:
        target = await self._resolve_schedule(arguments)
        _editable_target(target)
        return await self._lifecycle(arguments, call_id=call_id, action="resume", target=target)

    async def _cancel_schedule(self, arguments: Mapping[str, Any], *, call_id: str) -> dict[str, Any]:
        return await self._lifecycle(arguments, call_id=call_id, action="cancel")

    async def _lifecycle(
        self,
        arguments: Mapping[str, Any],
        *,
        call_id: str,
        action: str,
        target: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved = target if target is not None else await self._resolve_schedule(arguments)
        reason = arguments.get("reason")
        scope = arguments.get("scope")
        return await self._client.schedule_action(
            tenant_id=self._context.tenant_id,
            schedule_id=uuid.UUID(str(resolved["schedule_id"])),
            actor_user_id=self._context.actor_user_id,
            action=action,
            # 同一次工具调用的重试携带同一个幂等身份；换一次调用就是另一条命令。
            idempotency_key=submission_idempotency_key(self._context, call_id),
            scope=scope if isinstance(scope, str) else None,
            reason=reason if isinstance(reason, str) else None,
            trace_id=self._context.trace_id,
        )

    async def _list_schedules(self, arguments: Mapping[str, Any], *, call_id: str) -> dict[str, Any]:
        return await self._client.list_schedules(
            tenant_id=self._context.tenant_id,
            actor_user_id=self._context.actor_user_id,
            trace_id=self._context.trace_id,
            agent_id=str(self._context.agent.id),
            **_query(arguments),
        )

    async def _get_task(self, arguments: Mapping[str, Any], *, call_id: str) -> dict[str, Any]:
        data = await self._client.get_task(
            tenant_id=self._context.tenant_id,
            task_id=_uuid_arg(arguments, "task_id"),
            trace_id=self._context.trace_id,
            actor_user_id=self._context.actor_user_id,
        )
        return _task_summary(data)

    async def _list_tasks(self, arguments: Mapping[str, Any], *, call_id: str) -> dict[str, Any]:
        data = await self._client.list_tasks(
            tenant_id=self._context.tenant_id,
            trace_id=self._context.trace_id,
            actor_user_id=self._context.actor_user_id,
            **_query(arguments),
        )
        items = data.get("items") or []
        return {**data, "items": [_task_summary(item) for item in items if isinstance(item, Mapping)]}

    async def _cancel_task(self, arguments: Mapping[str, Any], *, call_id: str) -> dict[str, Any]:
        return await self._client.cancel_task(
            tenant_id=self._context.tenant_id,
            task_id=_uuid_arg(arguments, "task_id"),
            trace_id=self._context.trace_id,
            actor_user_id=self._context.actor_user_id,
        )


def _draft_card(response: Mapping[str, Any], *, kind: DraftKind) -> WebchatScheduleDraftReady:
    """Worker 的 Draft 回执 → 卡片公开 DTO；坏回执显式失败（不把半张卡片写进会话）。"""
    try:
        preview = response["preview"]
        return WebchatScheduleDraftReady(
            draft_id=uuid.UUID(str(response["draft_id"])),
            draft_hash=response["draft_hash"],
            kind=kind,
            mode=preview["mode"],
            preview=SchedulePreview.model_validate(preview),
            expires_at=response["expires_at"],
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise TaskToolError(
            ErrorCode.COMMON_INTERNAL_ERROR.value, "the draft service returned an invalid response"
        ) from exc


def _editable_target(target: Mapping[str, Any]) -> None:
    """终态任务只能新建：不改原时间、也不恢复（design §3.2.10；ONCE 准入失败同理）。"""
    status = target.get("status")
    if status in TERMINAL_SCHEDULE_STATUSES:
        raise TaskToolError(
            ErrorCode.SCHEDULE_STATE_INVALID.value,
            "this schedule already ended; create a new schedule instead of reviving or "
            "rescheduling it",
            {"status": status, "repair": "CREATE_NEW"},
        )


__all__ = [
    "CANCEL_SCHEDULE_TOOL",
    "CANCEL_TASK_TOOL",
    "DRAFT_SCHEDULE_TOOL",
    "DRAFT_SCHEDULE_UPDATE_TOOL",
    "GET_SCHEDULE_TOOL",
    "GET_TASK_TOOL",
    "LIST_SCHEDULES_TOOL",
    "LIST_TASKS_TOOL",
    "PAUSE_SCHEDULE_TOOL",
    "RESUME_SCHEDULE_TOOL",
    "BackgroundTaskToolSet",
]
