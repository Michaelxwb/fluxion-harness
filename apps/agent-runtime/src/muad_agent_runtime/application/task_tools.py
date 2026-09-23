"""Runtime 内置任务工具（docs/04 §7.5，模块 09 API-01~08 的 Agent 入口）。

`create_schedule / list_schedules / update_schedule / delete_schedule /
get_task / list_tasks / cancel_task` 全部经 Agent Worker Internal API，不直写 task schema；
actor 只取自已认证 Run 上下文（工具参数里没有 actor 字段，无法伪造），Worker 端再按
`X-Actor-User-Id` 校验归属，别人的 Task/Schedule 读不到也改不了。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolHandler, ToolRegistry
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import ResolvedSkill, ScheduleSpec
from pydantic import ValidationError

from .task_client import TaskSubmissionContext, WorkerTaskClient

CREATE_SCHEDULE_TOOL = "create_schedule"
LIST_SCHEDULES_TOOL = "list_schedules"
UPDATE_SCHEDULE_TOOL = "update_schedule"
DELETE_SCHEDULE_TOOL = "delete_schedule"
GET_TASK_TOOL = "get_task"
LIST_TASKS_TOOL = "list_tasks"
CANCEL_TASK_TOOL = "cancel_task"
SCHEDULE_ROUTE_REQUIRED = "SCHEDULE_DELIVERY_ROUTE_REQUIRED"
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
_SCHEDULE_SPEC: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["CRON", "ONCE"]},
        "cron": _STRING,
        "run_at": {"type": "string", "description": "ISO8601 with offset"},
        "timezone": {"type": "string", "description": "IANA timezone, e.g. Asia/Shanghai"},
    },
    "required": ["type", "timezone"],
}
TEMPLATE_HINT = (
    "input_template may use {{fire_date}}, {{previous_day}} and {{fire_time}}, rendered "
    "in the schedule timezone at each fire."
)


class TaskToolError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _schema(properties: Mapping[str, Any], required: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def _error(code: str, message: str) -> str:
    return json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False)


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


def _spec_arg(arguments: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = _object_arg(arguments, "schedule")
    if raw is None:
        return None
    try:
        return ScheduleSpec.model_validate(raw).model_dump(mode="json")
    except ValidationError as exc:
        raise TaskToolError(ErrorCode.COMMON_VALIDATION_ERROR.value, "invalid schedule") from exc


def _query(arguments: Mapping[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if isinstance(arguments.get("status"), str):
        params["status"] = arguments["status"]
    page = arguments.get("page")
    params["page"] = page if isinstance(page, int) and page >= 1 else 1
    return params


def _task_summary(data: Mapping[str, Any]) -> dict[str, Any]:
    return {key: data.get(key) for key in TASK_SUMMARY_KEYS}


Handler = Callable[[Mapping[str, Any]], Awaitable[Any]]
ToolSpec = tuple[str, str, dict[str, Any], ToolEffect, Handler]


class BackgroundTaskToolSet:
    def __init__(
        self,
        *,
        client: WorkerTaskClient,
        context: TaskSubmissionContext,
        skills: Sequence[ResolvedSkill],
    ) -> None:
        self._client = client
        self._context = context
        self._skills = {skill.key: skill for skill in skills}

    def register(self, registry: ToolRegistry) -> None:
        for definition in self._definitions():
            registry.register(definition)

    def _wrap(self, handler: Handler) -> ToolHandler:
        async def run(arguments: Mapping[str, Any]) -> str:
            try:
                return json.dumps(await handler(arguments), ensure_ascii=False, default=str)
            except TaskToolError as exc:
                return _error(exc.code, exc.message)
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
            )
            for name, description, schema, effect, handler in (
                *self._schedule_specs(),
                *self._task_specs(),
            )
        )

    def _schedule_specs(self) -> tuple[ToolSpec, ...]:
        return (
            (
                CREATE_SCHEDULE_TOOL,
                "Create a recurring (CRON) or one-time (ONCE) schedule that runs an effective "
                "skill in the background and delivers the final result to this chat. "
                + TEMPLATE_HINT,
                _schema(
                    {
                        "skill_key": _STRING,
                        "name": _STRING,
                        "input_template": _OBJECT,
                        "schedule": _SCHEDULE_SPEC,
                    },
                    ("skill_key", "schedule"),
                ),
                ToolEffect.WRITE,
                self._create_schedule,
            ),
            (
                LIST_SCHEDULES_TOOL,
                "List the current user's schedules.",
                _schema({"status": _STRING, "page": _PAGE}, ()),
                ToolEffect.READ,
                self._list_schedules,
            ),
            (
                UPDATE_SCHEDULE_TOOL,
                "Update name, input_template or timing of one of the user's schedules; only "
                "future fires are affected. " + TEMPLATE_HINT,
                _schema(
                    {
                        "schedule_id": _STRING,
                        "name": _STRING,
                        "input_template": _OBJECT,
                        "schedule": _SCHEDULE_SPEC,
                    },
                    ("schedule_id",),
                ),
                ToolEffect.WRITE,
                self._update_schedule,
            ),
            (
                DELETE_SCHEDULE_TOOL,
                "Delete one of the user's schedules; tasks already created keep running.",
                _schema({"schedule_id": _STRING}, ("schedule_id",)),
                ToolEffect.WRITE,
                self._delete_schedule,
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
            ),
            (
                LIST_TASKS_TOOL,
                "List the current user's background tasks, newest first.",
                _schema({"status": _STRING, "page": _PAGE}, ()),
                ToolEffect.READ,
                self._list_tasks,
            ),
            (
                CANCEL_TASK_TOOL,
                "Cancel one of the user's background tasks that has not finished yet.",
                _schema({"task_id": _STRING}, ("task_id",)),
                ToolEffect.WRITE,
                self._cancel_task,
            ),
        )

    def _skill(self, arguments: Mapping[str, Any]) -> ResolvedSkill:
        key = arguments.get("skill_key")
        skill = self._skills.get(key.strip()) if isinstance(key, str) else None
        if skill is None:
            raise TaskToolError("SKILL_NOT_EFFECTIVE", f"skill is not effective for this run: {key}")
        return skill

    def _schedule_key(self, arguments: Mapping[str, Any]) -> str:
        """同一 Run 内同参数重试复用同一 Schedule（Worker 侧 task_submission 幂等）。"""
        canonical = json.dumps(dict(arguments), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        return f"run:{self._context.source_run_id or 'ad-hoc'}:schedule:{digest}"

    async def _create_schedule(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        skill = self._skill(arguments)
        spec = _spec_arg(arguments)
        if spec is None:
            raise TaskToolError(ErrorCode.COMMON_VALIDATION_ERROR.value, "schedule is required")
        if self._context.delivery_route is None:
            raise TaskToolError(SCHEDULE_ROUTE_REQUIRED, "schedules need an IM conversation to deliver to")
        name = arguments.get("name")
        return await self._client.create_schedule(
            tenant_id=self._context.tenant_id,
            agent_id=self._context.agent.id,
            actor_user_id=self._context.actor_user_id,
            intent_key=skill.key,
            skill=skill,
            input_template=_object_arg(arguments, "input_template") or {},
            schedule=spec,
            delivery_route=self._context.delivery_route,
            idempotency_key=self._schedule_key(arguments),
            name=name if isinstance(name, str) and name.strip() else None,
            trace_id=self._context.trace_id,
            locale=self._context.locale,
        )

    async def _list_schedules(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return await self._client.list_schedules(
            tenant_id=self._context.tenant_id,
            actor_user_id=self._context.actor_user_id,
            trace_id=self._context.trace_id,
            agent_id=str(self._context.agent.id),
            **_query(arguments),
        )

    async def _update_schedule(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        name = arguments.get("name")
        return await self._client.update_schedule(
            tenant_id=self._context.tenant_id,
            schedule_id=_uuid_arg(arguments, "schedule_id"),
            actor_user_id=self._context.actor_user_id,
            name=name if isinstance(name, str) and name.strip() else None,
            schedule=_spec_arg(arguments),
            input_template=_object_arg(arguments, "input_template"),
            trace_id=self._context.trace_id,
        )

    async def _delete_schedule(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return await self._client.delete_schedule(
            tenant_id=self._context.tenant_id,
            schedule_id=_uuid_arg(arguments, "schedule_id"),
            actor_user_id=self._context.actor_user_id,
            trace_id=self._context.trace_id,
        )

    async def _get_task(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        data = await self._client.get_task(
            tenant_id=self._context.tenant_id,
            task_id=_uuid_arg(arguments, "task_id"),
            trace_id=self._context.trace_id,
            actor_user_id=self._context.actor_user_id,
        )
        return _task_summary(data)

    async def _list_tasks(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        data = await self._client.list_tasks(
            tenant_id=self._context.tenant_id,
            trace_id=self._context.trace_id,
            actor_user_id=self._context.actor_user_id,
            **_query(arguments),
        )
        items = data.get("items") or []
        return {**data, "items": [_task_summary(item) for item in items if isinstance(item, Mapping)]}

    async def _cancel_task(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return await self._client.cancel_task(
            tenant_id=self._context.tenant_id,
            task_id=_uuid_arg(arguments, "task_id"),
            trace_id=self._context.trace_id,
            actor_user_id=self._context.actor_user_id,
        )


__all__ = [
    "CANCEL_TASK_TOOL",
    "CREATE_SCHEDULE_TOOL",
    "DELETE_SCHEDULE_TOOL",
    "GET_TASK_TOOL",
    "LIST_SCHEDULES_TOOL",
    "LIST_TASKS_TOOL",
    "UPDATE_SCHEDULE_TOOL",
    "BackgroundTaskToolSet",
]
