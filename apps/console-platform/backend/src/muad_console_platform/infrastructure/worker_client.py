"""Console → Worker Admin API 的有界 HTTP 客户端（设计 §3.4 / API-17）。

- 共享一个 `httpx.AsyncClient`，显式 timeout；不做无界重试，失败由调用方决定；
- 透传已认证租户、内部服务身份、trace 与 locale；
- 上游业务码原样上抛；transport/封套异常统一 `COMMON_INTERNAL_ERROR`，
  且不把上游响应正文写入异常或日志（避免密钥/敏感数据外泄）。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from muad_api import AppError
from muad_api.error_codes import ErrorCode

logger = logging.getLogger(__name__)

TASKS_PATH = "/internal/admin/tasks"
SCHEDULES_PATH = "/internal/admin/schedules"
DEFAULT_TIMEOUT_SEC = 5.0
INTERNAL_SERVICE_HEADER = "X-Internal-Service"
TENANT_HEADER = "X-Tenant-Id"
TRACE_HEADER = "X-Trace-Id"


def _error_code(payload: Any) -> str:
    if isinstance(payload, dict):
        code = payload.get("code")
        if isinstance(code, str) and code:
            return code
    return str(ErrorCode.COMMON_INTERNAL_ERROR)


def _decode(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


class WorkerAdminClient:
    def __init__(
        self,
        base_url: str,
        *,
        service_token: str | None = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
        locale: str = "zh-CN",
    ) -> None:
        headers = {"Accept-Language": locale}
        if service_token:
            headers[INTERNAL_SERVICE_HEADER] = service_token
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_sec,
            transport=transport,
            headers=headers,
        )

    async def list_tasks(
        self, *, tenant_id: str, trace_id: str = "", **params: Any
    ) -> dict[str, Any]:
        return await self._request(
            "GET", TASKS_PATH, tenant_id=tenant_id, trace_id=trace_id, params=params
        )

    async def get_task(
        self, *, tenant_id: str, task_id: Any, trace_id: str = ""
    ) -> dict[str, Any]:
        return await self._request(
            "GET", f"{TASKS_PATH}/{task_id}", tenant_id=tenant_id, trace_id=trace_id
        )

    async def cancel_task(
        self, *, tenant_id: str, task_id: Any, trace_id: str = ""
    ) -> dict[str, Any]:
        return await self._request(
            "POST", f"{TASKS_PATH}/{task_id}/cancel", tenant_id=tenant_id, trace_id=trace_id
        )

    async def list_schedules(
        self, *, tenant_id: str, trace_id: str = "", **params: Any
    ) -> dict[str, Any]:
        return await self._request(
            "GET", SCHEDULES_PATH, tenant_id=tenant_id, trace_id=trace_id, params=params
        )

    async def get_schedule(
        self, *, tenant_id: str, schedule_id: Any, trace_id: str = ""
    ) -> dict[str, Any]:
        return await self._request(
            "GET", f"{SCHEDULES_PATH}/{schedule_id}", tenant_id=tenant_id, trace_id=trace_id
        )

    async def pause_schedule(
        self, *, tenant_id: str, schedule_id: Any, trace_id: str = ""
    ) -> dict[str, Any]:
        return await self._request(
            "PUT",
            f"{SCHEDULES_PATH}/{schedule_id}/pause",
            tenant_id=tenant_id,
            trace_id=trace_id,
        )

    async def resume_schedule(
        self, *, tenant_id: str, schedule_id: Any, trace_id: str = ""
    ) -> dict[str, Any]:
        return await self._request(
            "PUT",
            f"{SCHEDULES_PATH}/{schedule_id}/resume",
            tenant_id=tenant_id,
            trace_id=trace_id,
        )

    async def delete_schedule(
        self, *, tenant_id: str, schedule_id: Any, trace_id: str = ""
    ) -> dict[str, Any]:
        return await self._request(
            "DELETE", f"{SCHEDULES_PATH}/{schedule_id}", tenant_id=tenant_id, trace_id=trace_id
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        tenant_id: str,
        params: dict[str, Any] | None = None,
        trace_id: str = "",
    ) -> dict[str, Any]:
        headers = {TENANT_HEADER: tenant_id}
        if trace_id:
            headers[TRACE_HEADER] = trace_id
        try:
            response = await self._client.request(
                method,
                path,
                params={key: value for key, value in (params or {}).items() if value is not None},
                headers=headers,
            )
        except httpx.HTTPError:
            # 只记录方法与路径，不带上游正文/URL 查询串。
            logger.warning("worker_admin_request_failed method=%s path=%s", method, path)
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from None
        payload = _decode(response)
        if response.status_code >= 400:
            raise AppError(_error_code(payload))
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            logger.warning("worker_admin_envelope_invalid method=%s path=%s", method, path)
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return data
