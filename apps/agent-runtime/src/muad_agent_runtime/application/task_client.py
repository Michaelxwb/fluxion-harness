"""Agent Runtime → Agent Worker 的后台任务/定时任务客户端（docs/07 §5）。

- 只走 Internal HTTP，不直接读写 task schema；
- 传播已认证租户、Run 的 actor、trace、locale 与幂等键；
- 冻结 execution snapshot（不含密钥）后再提交，重复提交按幂等键复用；
- 显式 timeout，不做无界重试。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
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
    TaskType,
    WorkspaceRef,
    snapshot_hash,
)
from muad_contracts import build_task_snapshot as build_execution_snapshot
from muad_contracts.platform_settings import TaskSettings

TASKS_PATH = "/internal/tasks"
SCHEDULES_PATH = "/internal/schedules"
SCHEDULE_DRAFTS_PATH = "/internal/schedule-drafts"
DEFAULT_TIMEOUT_SEC = 5.0
IDEMPOTENCY_HEADER = "Idempotency-Key"
INTERNAL_SERVICE_HEADER = "X-Internal-Service"
TENANT_HEADER = "X-Tenant-Id"
TRACE_HEADER = "X-Trace-Id"
ACTOR_HEADER = "X-Actor-User-Id"
AGENT_HEADER = "X-Agent-Id"
#: 入口 surface：Draft 的投递意图由它决定（Worker 用共享通道词表判能力，Runtime 不点名渠道）。
ENTRY_CHANNEL_HEADER = "X-Entry-Channel"

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
    #: 产生这条提交的会话：Draft 确认卡片写进这里（与 Run 同一会话）。
    conversation_id: uuid.UUID | None = None
    delivery_route: DeliveryRouteInput | None = None
    #: Run 的入口 surface（`ChannelContext.type` 原值）；Draft 的投递能力与确认来源由它决定。
    entry_channel: str | None = None
    trace_id: str = ""
    locale: str = "zh-CN"
    execution_timeout_sec: int = field(default_factory=lambda: TaskSettings().execution_timeout_sec)


def build_task_snapshot(
    context: TaskSubmissionContext, skill: ResolvedSkill, *, argv: tuple[str, ...] = ()
) -> tuple[dict[str, Any], str]:
    """冻结本次 Task 所需版本键：只含要执行的这一个 Skill，密钥字段不进入快照。"""
    snapshot = build_execution_snapshot(
        agent=context.agent,
        model=context.model,
        skill=skill,
        workspace=WorkspaceRef(
            tenant_id=context.tenant_id,
            user_id=context.actor_user_id,
            agent_id=context.agent.id,
        ),
        mcp_servers=context.mcp_servers,
        argv=argv,
        execution_timeout_sec=context.execution_timeout_sec,
    )
    return snapshot, snapshot_hash(snapshot)


def submission_idempotency_key(context: TaskSubmissionContext, call_id: str) -> str:
    """A standalone submission retries one explicit call, never merges equal inputs."""
    run_part = str(context.source_run_id) if context.source_run_id else "ad-hoc"
    return f"run:{run_part}:call:{call_id}"


class WorkerTaskClient:
    def __init__(
        self,
        base_url: str,
        *,
        service_token: str | None = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._service_token = service_token
        # 请求用绝对 URL 组装：注入的共享池客户端没有 base_url（一个池服务多个目标）。
        self._base_url = base_url.rstrip("/")
        # 装配根注入的业务池由**装配根拥有并关闭**（本实例只借用）；未注入时自建自关。
        self._owns_client = client is None
        # 自建时按单一目标配 base_url（请求仍用绝对 URL，注入共享池时同样成立）。
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=timeout_sec, transport=transport
        )

    async def submit_task(
        self,
        context: TaskSubmissionContext,
        *,
        skill: ResolvedSkill,
        input_data: Mapping[str, Any],
        call_id: str,
        argv: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        snapshot, frozen_hash = build_task_snapshot(context, skill, argv=argv)
        route = context.delivery_route
        body: dict[str, Any] = {
            "tenant_id": context.tenant_id,
            "agent_id": str(context.agent.id),
            "actor_user_id": str(context.actor_user_id),
            "source_run_id": str(context.source_run_id) if context.source_run_id else None,
            "task_type": str(TaskType.SKILL),
            "skill_id": str(skill.skill_id),
            "skill_artifact_id": str(skill.artifact_id),
            "skill_execution_mode": str(skill.execution_mode),
            "input": dict(input_data),
            "execution_snapshot": snapshot,
            "execution_snapshot_schema_version": TASK_SNAPSHOT_SCHEMA_VERSION,
            "snapshot_hash": frozen_hash,
            "idempotency_key": submission_idempotency_key(context, call_id),
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

    async def create_schedule_draft(
        self,
        *,
        tenant_id: str,
        agent_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        entry_channel: str | None,
        draft: Mapping[str, Any],
        idempotency_key: str,
        trace_id: str = "",
        locale: str = "zh-CN",
    ) -> dict[str, Any]:
        """API-01：提交一条 Draft 请求，拿回 draft_id/hash/预览/过期时刻。

        身份（owner/agent/入口）全部走可信头，body 不接受；Runtime 没有确认能力——
        确认是 surface 在用户显式动作后调用的另一个端点（API-27）。
        """
        return await self._request(
            "POST",
            SCHEDULE_DRAFTS_PATH,
            tenant_id=tenant_id,
            trace_id=trace_id,
            locale=locale,
            json_body=dict(draft),
            idempotency_key=idempotency_key,
            actor_user_id=actor_user_id,
            agent_id=agent_id,
            entry_channel=entry_channel,
        )

    async def get_schedule(
        self,
        *,
        tenant_id: str,
        schedule_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        trace_id: str = "",
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"{SCHEDULES_PATH}/{schedule_id}",
            tenant_id=tenant_id,
            trace_id=trace_id,
            actor_user_id=actor_user_id,
        )

    async def schedule_action(
        self,
        *,
        tenant_id: str,
        schedule_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        action: str,
        idempotency_key: str,
        scope: str | None = None,
        reason: str | None = None,
        trace_id: str = "",
    ) -> dict[str, Any]:
        """API-04/05/06：暂停/恢复/取消。动作幂等身份由调用方给出（同一次工具调用重试同键）。"""
        body: dict[str, Any] = {"action": action}
        if scope is not None:
            body["scope"] = scope
        if reason is not None:
            body["reason"] = reason
        return await self._request(
            "POST",
            f"{SCHEDULES_PATH}/{schedule_id}/{action}",
            tenant_id=tenant_id,
            trace_id=trace_id,
            json_body=body,
            idempotency_key=idempotency_key,
            actor_user_id=actor_user_id,
        )

    async def aclose(self) -> None:
        if self._owns_client:
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
        agent_id: uuid.UUID | None = None,
        entry_channel: str | None = None,
    ) -> dict[str, Any]:
        headers = {TENANT_HEADER: tenant_id, "Accept-Language": locale}
        if actor_user_id is not None:
            headers[ACTOR_HEADER] = str(actor_user_id)
        if agent_id is not None:
            headers[AGENT_HEADER] = str(agent_id)
        if entry_channel:
            headers[ENTRY_CHANNEL_HEADER] = entry_channel
        if self._service_token:
            headers[INTERNAL_SERVICE_HEADER] = self._service_token
        if trace_id:
            headers[TRACE_HEADER] = trace_id
        if idempotency_key:
            headers[IDEMPOTENCY_HEADER] = idempotency_key
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
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
