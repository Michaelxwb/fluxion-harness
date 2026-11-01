"""Console → Runtime 经验生命周期写入口客户端。"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.learning_admin import LearningActionItem, LearningJobItem

logger = logging.getLogger(__name__)

BASE = "/internal/admin"
DEFAULT_TIMEOUT_SEC = 10.0
INTERNAL_SERVICE_HEADER = "X-Internal-Service"
TENANT_HEADER = "X-Tenant-Id"
TRACE_HEADER = "X-Trace-Id"
ACTOR_HEADER = "X-Actor-Id"
IDEMPOTENCY_HEADER = "Idempotency-Key"


def _error_code(payload: Any) -> str:
    if isinstance(payload, dict):
        code = payload.get("code")
        if isinstance(code, str) and code:
            return code
    return str(ErrorCode.COMMON_INTERNAL_ERROR)


class RuntimeLearningClient:
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
            base_url=base_url, timeout=timeout_sec, transport=transport, headers=headers
        )

    async def replay(
        self,
        *,
        tenant_id: str,
        user_id: uuid.UUID,
        key: str,
        actor_id: uuid.UUID,
        command_id: str,
        trace_id: str = "",
    ) -> LearningJobItem:
        data = await self._request(
            "POST",
            f"{BASE}/users/{user_id}/experiences/{key}/replay",
            tenant_id=tenant_id,
            actor_id=actor_id,
            command_id=command_id,
            trace_id=trace_id,
        )
        return LearningJobItem.model_validate(data)

    async def action(
        self,
        action: str,
        *,
        tenant_id: str,
        user_id: uuid.UUID,
        key: str,
        actor_id: uuid.UUID,
        command_id: str,
        reason: str | None,
        trace_id: str = "",
    ) -> LearningActionItem:
        data = await self._request(
            "POST",
            f"{BASE}/users/{user_id}/experiences/{key}/{action}",
            tenant_id=tenant_id,
            actor_id=actor_id,
            command_id=command_id,
            trace_id=trace_id,
            json={"reason": reason},
        )
        return LearningActionItem.model_validate(data)

    async def job(self, *, tenant_id: str, job_id: uuid.UUID) -> LearningJobItem:
        data = await self._request(
            "GET", f"{BASE}/learning/jobs/{job_id}", tenant_id=tenant_id
        )
        return LearningJobItem.model_validate(data)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        tenant_id: str,
        actor_id: uuid.UUID | None = None,
        command_id: str | None = None,
        trace_id: str = "",
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {TENANT_HEADER: tenant_id}
        if actor_id is not None:
            headers[ACTOR_HEADER] = str(actor_id)
        if command_id is not None:
            headers[IDEMPOTENCY_HEADER] = command_id
        if trace_id:
            headers[TRACE_HEADER] = trace_id
        try:
            response = await self._client.request(method, path, json=json, headers=headers)
        except httpx.HTTPError:
            logger.warning("runtime_learning_request_failed method=%s path=%s", method, path)
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code >= 400:
            raise AppError(_error_code(payload))
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            logger.warning("runtime_learning_envelope_invalid method=%s path=%s", method, path)
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return data
