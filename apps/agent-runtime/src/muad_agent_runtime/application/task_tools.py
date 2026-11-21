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
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from muad_agent_core.tools import ToolConcurrency, ToolDefinition, ToolEffect, ToolHandler, ToolRegistry
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    SCHEDULE_CONFIRM_COMMAND,
    DeliveryRouteInput,
    DraftKind,
    ResolvedSkill,
    SchedulePreview,
)
from muad_contracts.enums import OUTBOUND_CHANNELS, ScheduleStatus
from muad_contracts.platform_settings import PlatformSettingsError, parse_platform_settings
from muad_contracts.webchat import WebchatScheduleDraftReady
from muad_logging import get_logger
from pydantic import ValidationError

from ..infrastructure.db import SessionFactoryProvider
from .ports import PlatformSettingsClient
from .schedule_authoring import (
    DeliveryTargetOption,
    ScheduleAuthoringContext,
    ScheduleAuthoringError,
    ScheduleAuthoringResult,
    compile_schedule_plan,
)
from .schedule_draft_events import publish_schedule_draft_ready
from .schedule_promotion import (
    PromotionError,
    PromotionFacts,
    load_promotion_facts,
    promotion_proposal,
)
from .task_client import (
    TaskSubmissionContext,
    WorkerTaskClient,
    submission_idempotency_key,
)

logger = get_logger(__name__)

DRAFT_SCHEDULE_TOOL = "draft_schedule"
DRAFT_SCHEDULE_UPDATE_TOOL = "draft_schedule_update"
LIST_DELIVERY_TARGETS_TOOL = "list_delivery_targets"
PROMOTE_RUN_TOOL = "promote_run"
LIST_SCHEDULES_TOOL = "list_schedules"
GET_SCHEDULE_TOOL = "get_schedule"
PAUSE_SCHEDULE_TOOL = "pause_schedule"
RESUME_SCHEDULE_TOOL = "resume_schedule"
CANCEL_SCHEDULE_TOOL = "cancel_schedule"
DELETE_SCHEDULE_TOOL = "delete_schedule"
GET_TASK_TOOL = "get_task"
LIST_TASKS_TOOL = "list_tasks"
CANCEL_TASK_TOOL = "cancel_task"

#: 终态任务：只能新建，不能改时间也不能恢复（design §3.2.10 / E-13）。
TERMINAL_SCHEDULE_STATUSES = frozenset({ScheduleStatus.ENDED.value, ScheduleStatus.CANCELLED.value})

TASK_SUMMARY_KEYS = (
    "task_id",
    "status",
    "task_type",
    "skill_execution_mode",
    "trigger_type",
    "schedule_id",
    "schedule_fire_id",
    "wait_kind",
    "cancel_requested",
    "result",
    "error_code",
    "error_message",
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
_PAYLOAD_KIND = {
    "type": "string",
    "enum": ["REMINDER", "SKILL_PLAN", "AGENT_TURN"],
    "description": (
        "Optional. When omitted, the platform derives it from the fields present. Never ask "
        "the user to choose a kind."
    ),
}
_PAYLOAD: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "kind": _PAYLOAD_KIND,
        # REMINDER
        "text": {**_STRING, "description": "REMINDER only: the fixed text to deliver."},
        # SKILL_PLAN：input 只走 stdin，argv 追加到脚本。
        "skill_key": {**_STRING, "description": "SKILL_PLAN only: effective skill key of this run."},
        "skill_id": {**_STRING, "description": "SKILL_PLAN only: skill UUID when already known."},
        "input": {
            "type": "object",
            "description": "SKILL_PLAN only: JSON written to stdin; no control fields.",
        },
        "argv": {
            "type": "array",
            "items": _STRING,
            "description": "SKILL_PLAN only: extra CLI arguments (supported in the schedule timezone).",
        },
        "window": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["DAY"]},
                "offset": {"type": "integer", "minimum": -31, "maximum": 31},
            },
            "additionalProperties": False,
        },
        # AGENT_TURN
        "prompt": {
            **_STRING,
            "description": (
                "AGENT_TURN only: the complete business prompt. Nobody can answer questions "
                "during a scheduled run; state the expected deliverable."
            ),
        },
        "model_ref": {**_STRING, "description": "AGENT_TURN only: optional model reference."},
        "tools_allow": {
            "type": "array",
            "items": _STRING,
            "description": (
                "AGENT_TURN only: tool policy such as SKILL:<skill_key>:<tool>, "
                "MCP:<server>:<tool>, PLATFORM:current_time. Empty means follow the owner."
            ),
        },
        "budget_ref": {
            **_STRING,
            "description": "AGENT_TURN only: budget source; the platform is the only source.",
        },
    },
    "required": [],
    "additionalProperties": False,
}
_DRAFT_ARGS: Mapping[str, Any] = {
    "instruction": {
        **_STRING,
        "description": "The user's original request, verbatim (max 4000 chars).",
    },
    "payload": _PAYLOAD,
    "session_target": {
        **_STRING,
        "description": (
            "Context target: isolated (default), current (creator conversation tail), "
            "session:<id> (persistent session) or main. Delivery is a separate choice."
        ),
    },
    "delivery": {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": ["NONE", "PUSH"]},
            "target": {
                **_STRING,
                "description": (
                    "PUSH only: a safe target label returned by list_delivery_targets. "
                    "Omit to use the source chat route when it is already an outbound channel."
                ),
            },
        },
        "required": ["intent"],
        "additionalProperties": False,
        "description": (
            "Optional delivery override. Normally OMIT: the platform derives delivery from "
            "the source conversation and the configured route. Never ask the user to choose "
            "a channel or target. Only set PUSH when the user explicitly asks to send to an "
            "external channel; never expose channel names or target labels in the question."
        ),
    },
    "timezone": {
        **_STRING,
        "description": "IANA time zone confirmed by the user, e.g. Asia/Shanghai. Never guess.",
    },
    "recurrence": _RECURRENCE,
    "name": _STRING,
    "context": {
        "type": "object",
        "properties": {"parameters": _OBJECT, "output": _OBJECT},
        "additionalProperties": False,
    },
    "confidence": {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
        "description": (
            "0..1 confidence that this plan matches the business request. The platform asks "
            "one business question instead of drafting when confidence is low."
        ),
    },
    "reason": {
        **_STRING,
        "description": (
            "One sentence in the user's language explaining why this plan matches the "
            "request; shown on the confirmation card."
        ),
    },
}


_PROMOTE_ARGS: Mapping[str, Any] = {
    "run_id": {
        **_STRING,
        "description": "The id of a completed Run whose successful skill call is reused.",
    },
    "instruction": {
        **_STRING,
        "description": "The user's original request for turning the run into a schedule.",
    },
    "timezone": _DRAFT_ARGS["timezone"],
    "recurrence": _RECURRENCE,
    "name": _STRING,
    "session_target": _DRAFT_ARGS["session_target"],
    "delivery": _DRAFT_ARGS["delivery"],
    "window": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["DAY"]},
            "offset": {"type": "integer", "minimum": -31, "maximum": 31},
        },
        "additionalProperties": False,
        "description": (
            "Optional data window. Defaults to the bound date: previous_day -> offset -1, "
            "otherwise 0."
        ),
    },
    "reason": _DRAFT_ARGS["reason"],
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


def _instruction_arg(arguments: Mapping[str, Any]) -> str:
    value = arguments.get("instruction")
    if not isinstance(value, str) or not value.strip():
        raise TaskToolError(
            ErrorCode.COMMON_VALIDATION_ERROR.value, "instruction is required"
        )
    return value.strip()


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


def _authoring_context(
    context: TaskSubmissionContext,
    targets: Sequence[DeliveryTargetOption] = (),
    default_timezone: str | None = None,
) -> ScheduleAuthoringContext:
    return ScheduleAuthoringContext(
        skills={skill.key: skill for skill in context.skills},
        mcp_servers=context.mcp_servers,
        entry_channel=context.entry_channel,
        delivery_route=context.delivery_route,
        delivery_targets=targets,
        conversation_id=context.conversation_id,
        default_timezone=default_timezone,
    )


class BackgroundTaskToolSet:
    """注册任务/定时任务工具。`session_factory` 只用于同回合写 Draft 确认卡片事件。"""

    def __init__(
        self,
        *,
        client: WorkerTaskClient,
        context: TaskSubmissionContext,
        skills: Sequence[ResolvedSkill],
        session_factory: SessionFactoryProvider | None = None,
        settings_client: PlatformSettingsClient | None = None,
        fallback_timezone: str | None = None,
    ) -> None:
        self._client = client
        self._context = context
        self._skills = {skill.key: skill for skill in skills}
        self._session_factory = session_factory
        self._settings_client = settings_client
        self._fallback_timezone = fallback_timezone

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
        draft_schema = _schema(_DRAFT_ARGS, ("instruction", "payload", "recurrence"))
        update_schema = _schema(
            {**_DRAFT_ARGS, "schedule_id": _STRING, "query": _STRING},
            (
                "instruction",
                "payload",
                "timezone",
                "recurrence",
                "schedule_id|query",
            ),
        )
        update_schema["required"] = ["instruction", "payload", "timezone", "recurrence"]
        return (
            (
                LIST_DELIVERY_TARGETS_TOOL,
                "List the user's safe outbound push targets. Call it only when the user "
                "explicitly asks to send to an external channel; never call it for ordinary "
                "schedule creation and never invent a target label. Normal tasks omit delivery "
                "and let the platform use the source conversation / configured route.",
                _schema({}, ()),
                ToolEffect.READ,
                self._list_delivery_targets,
                ToolConcurrency.SERIAL,
                None,
            ),
            (
                DRAFT_SCHEDULE_TOOL,
                "Prepare a NEW scheduled task for the user to confirm. It only creates a draft: "
                "nothing runs and nothing is saved until the user confirms the card in the chat. "
                "Decide from the business request, without asking the user to pick internals: "
                "pure notification -> REMINDER; fixed skill and fixed parameters -> SKILL_PLAN; "
                "runtime judgment, multiple tools or context -> AGENT_TURN. If the intent is "
                "ambiguous, ask ONE business question (for example whether the customer/scope "
                "is fixed) instead of guessing or offering an internal option menu. Delivery is "
                "a platform default, not a user choice: never ask about channels/targets and "
                "normally omit delivery. Call it once per independent customer/target. Omit "
                "timezone to use the platform default; ask only when the user's intent depends "
                "on a different IANA timezone.",
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
                PROMOTE_RUN_TOOL,
                "Turn one of the user's completed Runs into a NEW scheduled task draft. It reuses "
                "the exact skill, stdin input and argv recorded from a successful "
                "execute(target=skill) call in that run, binds date values to scheduler "
                "variables such as {{previous_day}}, and only creates a draft: nothing runs and "
                "nothing is saved until the user confirms the card. Use this instead of asking "
                "the user to restate parameters. Provide the schedule time and the user's "
                "instruction; omit timezone to use the platform default. Never invent skill "
                "parameters from prose.",
                _schema(_PROMOTE_ARGS, ("run_id", "instruction", "recurrence")),
                ToolEffect.WRITE,
                self._promote_run,
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
            (
                DELETE_SCHEDULE_TOOL,
                "Delete one of the user's schedules in any status. It is a soft delete from "
                "the user's list; Fire/Execution/event history is kept. Use it only when the "
                "user explicitly asks to delete that schedule.",
                _schema({"schedule_id": _STRING, "query": _STRING}, ()),
                ToolEffect.WRITE,
                self._delete_schedule,
                ToolConcurrency.SERIAL,
                None,
            ),
        )

    def _task_specs(self) -> tuple[ToolSpec, ...]:
        return (
            (
                GET_TASK_TOOL,
                "Get status and result of one of the user's background tasks. When the user "
                "asks to relay a completed task's result, copy result.text verbatim into your "
                "reply: do not summarize, condense, or paraphrase it unless the user explicitly "
                "asks for a summary. Its output files can be listed with "
                "list_attachments(scope=task, task_id=...).",
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

    async def _list_delivery_targets(
        self, arguments: Mapping[str, Any], *, call_id: str
    ) -> dict[str, Any]:
        targets = await self._delivery_targets()
        return {
            "targets": [
                {"label": target.label, "channel": target.route.channel} for target in targets
            ]
        }

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
        authoring = await self._authoring(arguments)
        if authoring.clarification is not None:
            return self._clarification(authoring)
        return await self._submit_draft(arguments, kind=kind, target=target, authoring=authoring)

    async def _promote_run(
        self, arguments: Mapping[str, Any], *, call_id: str
    ) -> dict[str, Any]:
        """S-13：从一次成功运行的真实 skill 调用提升为 Draft（参数不复推）。"""
        if self._session_factory is None:
            raise TaskToolError(
                ErrorCode.COMMON_INTERNAL_ERROR.value,
                "run history is unavailable in this execution context",
            )
        run_id = _uuid_arg(arguments, "run_id")
        try:
            facts = await load_promotion_facts(
                self._session_factory,
                tenant_id=self._context.tenant_id,
                actor_user_id=self._context.actor_user_id,
                agent_id=self._context.agent.id,
                run_id=run_id,
            )
        except PromotionError as exc:
            raise TaskToolError(exc.code, exc.message, exc.data) from exc
        timezone = arguments.get("timezone")
        if not isinstance(timezone, str) or not timezone.strip():
            timezone = await self._default_timezone(arguments)
        if not isinstance(timezone, str) or not timezone.strip():
            raise TaskToolError(
                ErrorCode.SCHEDULE_TIMEZONE_REQUIRED.value,
                "the platform default timezone is unavailable; ask the user for an IANA timezone",
            )
        proposal = promotion_proposal(facts, arguments, timezone=timezone)
        authoring = await self._authoring(proposal.arguments)
        if authoring.clarification is not None:
            return self._clarification(authoring)
        return await self._submit_draft(
            proposal.arguments,
            kind=DraftKind.CREATE,
            target=None,
            authoring=authoring,
            extra={"promotion": self._promotion_payload(proposal.facts)},
        )

    def _promotion_payload(self, facts: PromotionFacts) -> dict[str, Any]:
        """Tool receipt facts: what was copied and which dates were bound."""
        return {
            "run_id": str(facts.run_id),
            "tool_call_id": facts.tool_call_id,
            "skill_key": facts.skill_key,
            "skill_id": facts.skill_id,
            "executed_at": facts.executed_at.isoformat(),
            "input": dict(facts.input),
            "argv": list(facts.argv),
            "bindings": dict(facts.bindings),
            "source": "RECORDED_SKILL_CALL",
        }

    async def _submit_draft(
        self,
        arguments: Mapping[str, Any],
        *,
        kind: DraftKind,
        target: Mapping[str, Any] | None,
        authoring: ScheduleAuthoringResult,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Draft 的唯一下游：body → Worker API-01 → 同回合卡片 → 工具回执。"""
        plan = authoring.plan
        assert plan is not None
        body = self._draft_body(arguments, kind=kind, target=target, authoring=authoring)
        response = await self._client.create_schedule_draft(
            tenant_id=self._context.tenant_id,
            agent_id=self._context.agent.id,
            actor_user_id=self._context.actor_user_id,
            entry_channel=self._context.entry_channel,
            draft=body,
            idempotency_key=self._draft_key(body),
            trace_id=self._context.trace_id,
            locale=self._context.locale,
        )
        card = _draft_card(response, kind=kind)
        card = card.model_copy(
            update={
                "preview": card.preview.model_copy(
                    update={
                        "confidence": authoring.confidence,
                        "reason": authoring.reason,
                    }
                )
            }
        )
        seq = await self._publish_card(card)
        return self._draft_receipt(card, response, authoring=authoring, seq=seq, extra=extra)

    def _draft_receipt(
        self,
        card: WebchatScheduleDraftReady,
        response: Mapping[str, Any],
        *,
        authoring: ScheduleAuthoringResult,
        seq: int | None,
        extra: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        plan = authoring.plan
        assert plan is not None
        receipt = {
            "draft_id": str(card.draft_id),
            "draft_hash": card.draft_hash,
            "status": response.get("status"),
            "kind": card.kind.value,
            "mode": card.mode.value,
            "preview": card.preview.model_dump(mode="json"),
            "resolved_plan": plan.model_dump(mode="json"),
            "resolved_payload": plan.payload.model_dump(mode="json"),
            "confidence": authoring.confidence,
            "reason": authoring.reason,
            "budget_source": authoring.budget_source,
            "session_target": str(plan.session_target),
            "delivery": str(plan.delivery.intent),
            "tools_allow": authoring.tools_allow,
            "expires_at": response.get("expires_at"),
            "card_event_seq": seq,
            "card": "PUBLISHED" if seq is not None else "UNPUBLISHED",
            "next_step": self._next_step(card),
            "confirm_command": (
                f"{SCHEDULE_CONFIRM_COMMAND} {card.draft_id}"
                if self._is_push_surface()
                else None
            ),
        }
        if extra:
            receipt.update(extra)
        return receipt

    async def _authoring(self, arguments: Mapping[str, Any]) -> ScheduleAuthoringResult:
        targets = await self._delivery_targets()
        default_timezone = await self._default_timezone(arguments)
        try:
            return compile_schedule_plan(
                arguments,
                context=_authoring_context(self._context, targets, default_timezone),
            )
        except ScheduleAuthoringError as exc:
            raise TaskToolError(exc.code, exc.message, exc.data) from exc

    async def _default_timezone(self, arguments: Mapping[str, Any]) -> str | None:
        """Read the current platform timezone only when the caller did not provide one."""
        raw = arguments.get("timezone")
        if isinstance(raw, str) and raw.strip():
            return None
        if self._settings_client is None:
            return self._fallback_timezone
        try:
            snapshot = await self._settings_client.fetch_snapshot(
                tenant_id=self._context.tenant_id
            )
            return parse_platform_settings(snapshot.settings).locale.default_timezone
        except (AppError, PlatformSettingsError) as exc:
            raise TaskToolError(
                ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE.value,
                "platform settings are unavailable; cannot resolve the default timezone",
            ) from exc

    async def _delivery_targets(self) -> tuple[DeliveryTargetOption, ...]:
        """Read the owner's outbound targets; an unavailable list is an empty list, not a guess."""
        try:
            data = await self._client.list_delivery_targets(
                tenant_id=self._context.tenant_id,
                actor_user_id=self._context.actor_user_id,
                agent_id=self._context.agent.id,
                trace_id=self._context.trace_id,
            )
        except AppError:
            logger.warning("schedule_delivery_targets_unavailable")
            return ()
        options: list[DeliveryTargetOption] = []
        items = data.get("items") if isinstance(data, Mapping) else None
        if not isinstance(items, list):
            return ()
        for item in items:
            if not isinstance(item, Mapping):
                continue
            label = item.get("label")
            route = item.get("route")
            if not isinstance(label, str) or not isinstance(route, Mapping):
                continue
            try:
                parsed = DeliveryRouteInput.model_validate(route)
            except ValidationError:
                continue
            options.append(DeliveryTargetOption(label=label, route=parsed))
        return tuple(options)

    def _clarification(self, authoring: ScheduleAuthoringResult) -> dict[str, Any]:
        clarification = authoring.clarification
        assert clarification is not None
        return {
            "status": "NEEDS_CLARIFICATION",
            "needs_clarification": True,
            "confidence": authoring.confidence,
            "reason": authoring.reason,
            "question": clarification.question,
            "fields": list(clarification.fields),
            "next_step": (
                "Ask the user this ONE business question in their language. Do not offer "
                "internal choices, and do not create a draft until the answer is clear."
            ),
        }

    def _is_push_surface(self) -> bool:
        """聊天入口（能在创建会话里回复用户）才用文本确认；站内入口用卡片。"""
        return (self._context.entry_channel or "") in OUTBOUND_CHANNELS

    def _next_step(self, card: WebchatScheduleDraftReady) -> str:
        if self._is_push_surface():
            return (
                "Present this preview to the user. It only becomes a schedule when the user "
                f"replies `{SCHEDULE_CONFIRM_COMMAND} {card.draft_id}` in this chat; show them "
                "that exact command. You cannot confirm or create it yourself."
            )
        return (
            "Present this preview to the user; it only becomes a schedule when the user "
            "confirms it in the chat card. You cannot confirm or create it yourself."
        )

    def _draft_body(
        self,
        arguments: Mapping[str, Any],
        *,
        kind: DraftKind,
        target: Mapping[str, Any] | None,
        authoring: ScheduleAuthoringResult,
    ) -> dict[str, Any]:
        plan = authoring.plan
        assert plan is not None
        context = _context_arg(arguments)
        # 入口只作审计（design §2.3.6）：入口不是数据隔离边界，可见性仍按 user+agent。
        context["source"] = {
            "origin_bot_account_id": (
                str(self._context.entry_id) if self._context.entry_id is not None else None
            ),
            "origin_channel": self._context.entry_channel,
        }
        if (
            plan.session_target.kind in ("current", "main")
            and self._context.conversation_id is not None
        ):
            # current/main 的会话身份来自可信 Run 上下文，不让模型自报。
            context["session_conversation_id"] = str(self._context.conversation_id)
        body: dict[str, Any] = {
            "kind": kind.value,
            "instruction": _instruction_arg(arguments),
            "recurrence": dict(authoring.recurrence or {}),
            "context": context,
            "payload": plan.payload.model_dump(mode="json"),
            "session_target": str(plan.session_target),
            "delivery": plan.delivery.model_dump(mode="json", exclude_none=True),
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

    def _draft_key(self, body: Mapping[str, Any]) -> str:
        """同一个 Run 里同配置的 Draft 重试复用同一条（authoring 元数据不属于配置）。"""
        canonical = json.dumps(dict(body), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
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

    async def _delete_schedule(self, arguments: Mapping[str, Any], *, call_id: str) -> dict[str, Any]:
        target = await self._resolve_schedule(arguments)
        return await self._client.delete_schedule(
            tenant_id=self._context.tenant_id,
            schedule_id=uuid.UUID(str(target["schedule_id"])),
            actor_user_id=self._context.actor_user_id,
            idempotency_key=submission_idempotency_key(self._context, call_id),
            trace_id=self._context.trace_id,
        )

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
    "PROMOTE_RUN_TOOL",
    "RESUME_SCHEDULE_TOOL",
    "BackgroundTaskToolSet",
]
