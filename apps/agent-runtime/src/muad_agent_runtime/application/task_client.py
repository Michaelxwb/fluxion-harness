"""Agent Runtime → Agent Worker 的后台任务/定时任务客户端（docs/07 §5）。

- 只走 Internal HTTP，不直接读写 task schema；
- 传播已认证租户、Run 的 actor、trace、locale 与幂等键；
- 冻结 execution snapshot（不含密钥）后再提交，重复提交按幂等键复用；
- 显式 timeout，不做无界重试。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    TASK_SNAPSHOT_SCHEMA_VERSION,
    DeliveryRouteInput,
    ResolvedAgent,
    ResolvedMcpServer,
    ResolvedModel,
    ResolvedSkill,
    snapshot_hash,
)
from muad_contracts import build_task_snapshot as build_execution_snapshot

TASKS_PATH = "/internal/tasks"
SCHEDULES_PATH = "/internal/schedules"
DEFAULT_TIMEOUT_SEC = 5.0
IDEMPOTENCY_HEADER = "Idempotency-Key"
INTERNAL_SERVICE_HEADER = "X-Internal-Service"
TENANT_HEADER = "X-Tenant-Id"
TRACE_HEADER = "X-Trace-Id"
ACTOR_HEADER = "X-Actor-User-Id"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TaskSubmissionContext:
    """一次 Run 内提交后台任务的不可伪造上下文。"""

    tenant_id: str
    actor_user_id: uuid.UUID
    agent: ResolvedAgent
    model: ResolvedModel
    skills: tuple[ResolvedSkill, ...]
    mcp_servers: tuple[ResolvedMcpServer, ...] = ()
    source_run_id: uuid.UUID | None = None
    delivery_route: DeliveryRouteInput | None = None
    trace_id: str = ""
    locale: str = "zh-CN"


def build_task_snapshot(
    context: TaskSubmissionContext, skill: ResolvedSkill
) -> tuple[dict[str, Any], str]:
    """冻结本次 Task 所需版本键：只含要执行的这一个 Skill，密钥字段不进入快照。"""
    snapshot = build_execution_snapshot(
        agent=context.agent,
        model=context.model,
        skill=skill,
        mcp_servers=context.mcp_servers,
    )
    return snapshot, snapshot_hash(snapshot)


def submission_idempotency_key(
    context: TaskSubmissionContext, skill: ResolvedSkill, input_data: Mapping[str, Any]
) -> str:
    """同一 Run 内同 Skill 同输入重试复用同一 Task。"""
    canonical = json.dumps(input_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    run_part = str(context.source_run_id) if context.source_run_id else "ad-hoc"
    return f"run:{run_part}:skill:{skill.key}:{digest}"


class WorkerTaskClient:
    def __init__(
        self,
        base_url: str,
        *,
        service_token: str | None = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._service_token = service_token
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_sec, transport=transport)

    async def submit_task(
        self,
        context: TaskSubmissionContext,
        *,
        skill: ResolvedSkill,
        input_data: Mapping[str, Any],
        intent_key: str | None = None,
    ) -> dict[str, Any]:
        snapshot, frozen_hash = build_task_snapshot(context, skill)
        route = context.delivery_route
        body: dict[str, Any] = {
            "tenant_id": context.tenant_id,
            "agent_id": str(context.agent.id),
            "actor_user_id": str(context.actor_user_id),
            "source_run_id": str(context.source_run_id) if context.source_run_id else None,
            "intent_key": intent_key or skill.key,
            "skill_id": str(skill.skill_id),
            "skill_artifact_id": str(skill.artifact_id),
            "input": dict(input_data),
            "execution_snapshot": snapshot,
            "execution_snapshot_schema_version": TASK_SNAPSHOT_SCHEMA_VERSION,
            "snapshot_hash": frozen_hash,
            "idempotency_key": submission_idempotency_key(context, skill, input_data),
            "delivery_mode": "FINAL_ONLY" if route is not None else "NONE",
        }
        if route is not None:
            body["delivery_route"] = route.model_dump(mode="json")
        return await self._request(
            "POST",
            TASKS_PATH,
            tenant_id=context.tenant_id,
            trace_id=context.trace_id,
            locale=context.locale,
            json_body=body,
            idempotency_key=body["idempotency_key"],
        )

    async def get_task(
        self,
        *,
        tenant_id: str,
        task_id: uuid.UUID,
        trace_id: str = "",
        actor_user_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"{TASKS_PATH}/{task_id}",
            tenant_id=tenant_id,
            trace_id=trace_id,
            actor_user_id=actor_user_id,
        )

    async def list_tasks(
        self,
        *,
        tenant_id: str,
        trace_id: str = "",
        actor_user_id: uuid.UUID | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            TASKS_PATH,
            tenant_id=tenant_id,
            trace_id=trace_id,
            params=params,
            actor_user_id=actor_user_id,
        )

    async def cancel_task(
        self,
        *,
        tenant_id: str,
        task_id: uuid.UUID,
        trace_id: str = "",
        actor_user_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"{TASKS_PATH}/{task_id}/cancel",
            tenant_id=tenant_id,
            trace_id=trace_id,
            actor_user_id=actor_user_id,
        )

    async def list_schedules(
        self,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        trace_id: str = "",
        **params: Any,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            SCHEDULES_PATH,
            tenant_id=tenant_id,
            trace_id=trace_id,
            params=params,
            actor_user_id=actor_user_id,
        )

    async def create_schedule(
        self,
        *,
        tenant_id: str,
        agent_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        intent_key: str,
        skill: ResolvedSkill,
        input_template: Mapping[str, Any],
        schedule: Mapping[str, Any],
        delivery_route: DeliveryRouteInput,
        idempotency_key: str,
        name: str | None = None,
        trace_id: str = "",
        locale: str = "zh-CN",
    ) -> dict[str, Any]:
        body = {
            "name": name or f"{skill.key} schedule",
            "agent_id": str(agent_id),
            "actor_user_id": str(actor_user_id),
            "intent_key": intent_key,
            "skill_id": str(skill.skill_id),
            "input_template": dict(input_template),
            "schedule": dict(schedule),
            "delivery_route": delivery_route.model_dump(mode="json"),
        }
        return await self._request(
            "POST",
            SCHEDULES_PATH,
            tenant_id=tenant_id,
            trace_id=trace_id,
            locale=locale,
            json_body=body,
            idempotency_key=idempotency_key,
            actor_user_id=actor_user_id,
        )

    async def update_schedule(
        self,
        *,
        tenant_id: str,
        schedule_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        name: str | None = None,
        schedule: Mapping[str, Any] | None = None,
        input_template: Mapping[str, Any] | None = None,
        delivery_route: DeliveryRouteInput | None = None,
        trace_id: str = "",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if schedule is not None:
            body["schedule"] = dict(schedule)
        if input_template is not None:
            body["input_template"] = dict(input_template)
        if delivery_route is not None:
            body["delivery_route"] = delivery_route.model_dump(mode="json")
        return await self._request(
            "PUT",
            f"{SCHEDULES_PATH}/{schedule_id}",
            tenant_id=tenant_id,
            trace_id=trace_id,
            json_body=body,
            actor_user_id=actor_user_id,
        )

    async def delete_schedule(
        self,
        *,
        tenant_id: str,
        schedule_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        trace_id: str = "",
    ) -> dict[str, Any]:
        return await self._request(
            "DELETE",
            f"{SCHEDULES_PATH}/{schedule_id}",
            tenant_id=tenant_id,
            trace_id=trace_id,
            actor_user_id=actor_user_id,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        tenant_id: str,
        trace_id: str = "",
        locale: str = "zh-CN",
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        headers = {TENANT_HEADER: tenant_id, "Accept-Language": locale}
        if actor_user_id is not None:
            headers[ACTOR_HEADER] = str(actor_user_id)
        if self._service_token:
            headers[INTERNAL_SERVICE_HEADER] = self._service_token
        if trace_id:
            headers[TRACE_HEADER] = trace_id
        if idempotency_key:
            headers[IDEMPOTENCY_HEADER] = idempotency_key
        try:
            response = await self._client.request(
                method,
                path,
                params={key: value for key, value in (params or {}).items() if value is not None},
                json=json_body,
                headers=headers,
            )
        except httpx.HTTPError:
            logger.warning("worker_task_request_failed method=%s path=%s", method, path)
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from None
        payload: Any
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code >= 400:
            code = payload.get("code") if isinstance(payload, dict) else None
            raise AppError(code if isinstance(code, str) and code else ErrorCode.COMMON_INTERNAL_ERROR)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            logger.warning("worker_task_envelope_invalid method=%s path=%s", method, path)
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return data
