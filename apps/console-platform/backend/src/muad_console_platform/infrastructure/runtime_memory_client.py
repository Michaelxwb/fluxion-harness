"""Console → Runtime 记忆管理写入口的有界 HTTP 客户端（design §3.2.1 / §3.4.3 / §3.7）。

- Runtime 是唯一记忆写所有者：Console 只在管理员鉴权后把命令转发过去，**不直接 UPDATE runtime 表**；
- `Idempotency-Key` 原样透传为 Runtime 命令号 ⇒ 同一命令号重放返回首次回执，不产生第二次效果；
- 上游业务码（403/404/409/422）原样上抛；transport/封套异常统一 `COMMON_INTERNAL_ERROR`，
  且不把上游响应正文写入异常或日志（记忆内容与密钥都可能出现在正文里）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory_admin import MemoryAdminMutation, MemoryAdminRequest, MemoryCommandResponse

logger = logging.getLogger(__name__)

USERS_PATH = "/internal/admin/users"
MEMORY_PATH = "memory"
DEFAULT_TIMEOUT_SEC = 5.0
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


def _decode(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


class RuntimeMemoryClient:
    """只做转发：不重试、不本地改状态，也不替调用方决定失败语义。"""

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

    async def command(
        self,
        *,
        tenant_id: str,
        user_id: uuid.UUID,
        actor_id: uuid.UUID,
        command_id: str,
        mutation: MemoryAdminMutation,
        trace_id: str = "",
    ) -> MemoryCommandResponse:
        """受保护管理命令：actor 与 tenant 是 Console 已认证主体的属性，不由下游自选。"""
        body = MemoryAdminRequest(actor_id=actor_id, mutation=mutation)
        data = await self._request(
            "POST",
            f"{USERS_PATH}/{user_id}/{MEMORY_PATH}/commands",
            tenant_id=tenant_id,
            trace_id=trace_id,
            command_id=command_id,
            json=body.model_dump(mode="json", exclude_unset=True),
        )
        return MemoryCommandResponse.model_validate(data)

    async def clear(
        self,
        *,
        tenant_id: str,
        user_id: uuid.UUID,
        actor_id: uuid.UUID,
        command_id: str,
        trace_id: str = "",
    ) -> MemoryCommandResponse:
        """一命令原子清空：mutation 由 Runtime 按 `CLEAR` 构造，此处只送主体与命令号。"""
        data = await self._request(
            "DELETE",
            f"{USERS_PATH}/{user_id}/{MEMORY_PATH}",
            tenant_id=tenant_id,
            trace_id=trace_id,
            command_id=command_id,
            actor_id=actor_id,
        )
        return MemoryCommandResponse.model_validate(data)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        tenant_id: str,
        trace_id: str = "",
        command_id: str | None = None,
        actor_id: uuid.UUID | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {TENANT_HEADER: tenant_id}
        if trace_id:
            headers[TRACE_HEADER] = trace_id
        if command_id is not None:
            headers[IDEMPOTENCY_HEADER] = command_id
        if actor_id is not None:
            headers[ACTOR_HEADER] = str(actor_id)
        try:
            response = await self._client.request(method, path, json=json, headers=headers)
        except httpx.HTTPError:
            # 只记录方法与路径（含 user_id），不带上游正文/查询串。
            logger.warning("runtime_memory_request_failed method=%s path=%s", method, path)
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from None
        payload = _decode(response)
        if response.status_code >= 400:
            raise AppError(_error_code(payload))
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            logger.warning("runtime_memory_envelope_invalid method=%s path=%s", method, path)
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return data
