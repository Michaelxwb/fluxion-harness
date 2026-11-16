"""Gateway → Agent Worker 的任务中心内部客户端（design §3.4.6 API-12..19/API-27、§3.4.1）。

- **只走受检内部面**：服务身份（`X-Internal-Service`）+ 部署租户 + **已验证主体的 actor**
  （`X-Actor-User-Id`）。浏览器自报的 tenant/user 在这里没有位置——它们只从 `ChatPrincipal`
  导出，由调用方显式传入。
- **Agent 维度显式**：`X-Agent-Id` 只在主体当前授权集合已经收敛到该 Agent 时出现（回答/动作），
  Worker 侧据此再比对一次归属；本客户端不替调用方猜 Agent。
- **确认来源只有真 surface 能写**：`X-Confirmation-Surface` / `X-Confirmation-Ref` 由
  `TaskCenterService` 在真实用户动作（surface 路由 + 幂等键）下提供；模型工具路径没有这条通路。
- **错误原样上抛**：Owner 的稳定错误码（`SCHEDULE_NOT_FOUND`/`TASK_ACCESS_DENIED`/
  `ARTIFACT_VERSION_CONFLICT` …）按封套透传，传输失败显式 `COMMON_INTERNAL_ERROR`，不伪装结果。
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import httpx
from muad_api import AppError
from muad_api.context import current_request_id
from muad_api.error_codes import ErrorCode
from muad_api.security import INTERNAL_SERVICE_HEADER
from muad_contracts import ConfirmationSurface, ScheduleStatus

from .envelope import decode_json, error_code_from_payload, require_data_dict

logger = logging.getLogger(__name__)

SCHEDULES_PATH = "/internal/schedules"
SCHEDULE_PATH = "/internal/schedules/{schedule_id}"
SCHEDULE_FIRES_PATH = "/internal/schedules/{schedule_id}/fires"
SCHEDULE_ACTION_PATH = "/internal/schedules/{schedule_id}/{action}"
EXECUTION_PATH = "/internal/executions/{execution_id}"
PENDING_PATH = "/internal/task-center/pending"
BADGES_PATH = "/internal/task-center/badges"
HUMAN_RESPOND_PATH = "/internal/human-interactions/{interaction_id}/respond"
SCHEDULE_DRAFT_PATH = "/internal/schedule-drafts/{draft_id}"
SCHEDULE_DRAFT_STATUS_PATH = "/internal/schedule-drafts/{draft_id}/status"
SCHEDULE_CONFIRM_PATH = "/internal/schedule-drafts/{draft_id}/confirm"

TENANT_HEADER = "X-Tenant-Id"
ACTOR_HEADER = "X-Actor-User-Id"
AGENT_HEADER = "X-Agent-Id"
IDEMPOTENCY_HEADER = "Idempotency-Key"
CONFIRMATION_SURFACE_HEADER = "X-Confirmation-Surface"
CONFIRMATION_REF_HEADER = "X-Confirmation-Ref"
CALLER_SERVICE = "muad-im-gateway"
REQUEST_TIMEOUT_SEC = 10.0


class WorkerClient:
    """任务中心要用的 Worker 内部面（读取 + 用户动作 + Draft 确认）。"""

    def __init__(
        self,
        base_url: str,
        *,
        service_token: str | None = None,
        timeout_sec: float = REQUEST_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._service_token = service_token
        # 请求用绝对 URL 组装：注入的共享池客户端没有 base_url（一个池服务多个目标）。
        self._base_url = base_url.rstrip("/")
        # 装配根注入的业务池由**装配根拥有并关闭**（本实例只借用）；未注入时自建自关。
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_sec, transport=transport)

    async def list_schedules(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID | None,
        status: ScheduleStatus | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        params = {"page": str(page), "page_size": str(page_size)}
        if agent_id is not None:
            params["agent_id"] = str(agent_id)
        if status is not None:
            params["status"] = str(status)
        return await self._get(
            SCHEDULES_PATH, tenant_id=tenant_id, user_id=user_id, params=params
        )

    async def get_schedule(
        self, *, tenant_id: str, user_id: UUID, schedule_id: UUID
    ) -> dict[str, Any]:
        return await self._get(
            SCHEDULE_PATH.format(schedule_id=schedule_id), tenant_id=tenant_id, user_id=user_id
        )

    async def list_fires(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        schedule_id: UUID,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        return await self._get(
            SCHEDULE_FIRES_PATH.format(schedule_id=schedule_id),
            tenant_id=tenant_id,
            user_id=user_id,
            params={"page": str(page), "page_size": str(page_size)},
        )

    async def get_execution(
        self, *, tenant_id: str, user_id: UUID, execution_id: UUID
    ) -> dict[str, Any]:
        return await self._get(
            EXECUTION_PATH.format(execution_id=execution_id), tenant_id=tenant_id, user_id=user_id
        )

    async def list_pending(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID,
        kind: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        params = {"agent_id": str(agent_id), "page": str(page), "page_size": str(page_size)}
        if kind is not None:
            params["kind"] = kind
        return await self._get(
            PENDING_PATH, tenant_id=tenant_id, user_id=user_id, params=params
        )

    async def badges(
        self, *, tenant_id: str, user_id: UUID, agent_id: UUID
    ) -> dict[str, Any]:
        return await self._get(
            BADGES_PATH,
            tenant_id=tenant_id,
            user_id=user_id,
            params={"agent_id": str(agent_id)},
        )

    async def respond_human(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID | None,
        interaction_id: UUID,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        return await self._post(
            HUMAN_RESPOND_PATH.format(interaction_id=interaction_id),
            tenant_id=tenant_id,
            user_id=user_id,
            payload=payload,
            agent_id=agent_id,
            idempotency_key=idempotency_key,
        )

    async def schedule_action(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        schedule_id: UUID,
        action: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        return await self._post(
            SCHEDULE_ACTION_PATH.format(schedule_id=schedule_id, action=action),
            tenant_id=tenant_id,
            user_id=user_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    async def get_schedule_draft(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID,
        draft_id: UUID,
    ) -> dict[str, Any]:
        """API-01 读面：确认前按 id 取回本人的 Draft（hash/预览/过期），不接受自报 hash。"""
        return await self._get(
            SCHEDULE_DRAFT_PATH.format(draft_id=draft_id),
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
        )

    async def get_schedule_draft_status(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        draft_id: UUID,
    ) -> dict[str, Any]:
        """Draft 确认状态读面：surface 刷新后判断历史卡片是否还能确认。"""
        return await self._get(
            SCHEDULE_DRAFT_STATUS_PATH.format(draft_id=draft_id),
            tenant_id=tenant_id,
            user_id=user_id,
        )

    async def confirm_draft(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        draft_id: UUID,
        payload: dict[str, Any],
        surface: ConfirmationSurface,
        ref: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """API-27：确认来源必须由真实 surface 提供（模型工具上下文没有这条通路）。"""
        return await self._post(
            SCHEDULE_CONFIRM_PATH.format(draft_id=draft_id),
            tenant_id=tenant_id,
            user_id=user_id,
            payload=payload,
            idempotency_key=idempotency_key,
            confirmation=(surface, ref),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- 传输 -------------------------------------------------------------------

    def _headers(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID | None,
        idempotency_key: str | None,
        confirmation: tuple[ConfirmationSurface, str] | None,
    ) -> dict[str, str]:
        headers = {
            "X-Caller-Service": CALLER_SERVICE,
            TENANT_HEADER: tenant_id,
            ACTOR_HEADER: str(user_id),
        }
        if self._service_token:
            headers[INTERNAL_SERVICE_HEADER] = self._service_token
        if agent_id is not None:
            headers[AGENT_HEADER] = str(agent_id)
        if idempotency_key is not None:
            headers[IDEMPOTENCY_HEADER] = idempotency_key
        if confirmation is not None:
            surface, ref = confirmation
            headers[CONFIRMATION_SURFACE_HEADER] = str(surface)
            headers[CONFIRMATION_REF_HEADER] = ref
        request_id = current_request_id()
        if request_id:
            headers["X-Request-Id"] = request_id
        return headers

    async def _get(
        self,
        path: str,
        *,
        tenant_id: str,
        user_id: UUID,
        params: dict[str, str] | None = None,
        agent_id: UUID | None = None,
    ) -> dict[str, Any]:
        headers = self._headers(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            idempotency_key=None,
            confirmation=None,
        )
        try:
            response = await self._client.get(
                f"{self._base_url}{path}", params=params, headers=headers
            )
        except httpx.HTTPError as exc:
            raise _transport_failure(path) from exc
        return self._data(path, response)

    async def _post(
        self,
        path: str,
        *,
        tenant_id: str,
        user_id: UUID,
        payload: dict[str, Any],
        agent_id: UUID | None = None,
        idempotency_key: str,
        confirmation: tuple[ConfirmationSurface, str] | None = None,
    ) -> dict[str, Any]:
        headers = self._headers(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            idempotency_key=idempotency_key,
            confirmation=confirmation,
        )
        try:
            response = await self._client.post(
                f"{self._base_url}{path}", json=payload, headers=headers
            )
        except httpx.HTTPError as exc:
            raise _transport_failure(path) from exc
        return self._data(path, response)

    @staticmethod
    def _data(path: str, response: httpx.Response) -> dict[str, Any]:
        body = decode_json(response)
        if response.status_code >= 400:
            code = error_code_from_payload(body)
            logger.warning(
                "task_center_owner_http_error",
                extra={"path": path, "status": response.status_code, "code": code},
            )
            raise AppError(code)
        return require_data_dict(body)


def _transport_failure(path: str) -> AppError:
    """传输失败只记路径与异常类型（不记凭据/响应体），语义是"没拿到结论"。"""
    logger.warning("task_center_owner_transport_failure", extra={"path": path})
    return AppError(ErrorCode.COMMON_INTERNAL_ERROR)
