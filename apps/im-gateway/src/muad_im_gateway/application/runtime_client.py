from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, Protocol
from uuid import UUID

import httpx
from muad_api import AppError
from muad_api.context import current_request_id
from muad_api.error_codes import ErrorCode
from muad_contracts import RunRequest

from .envelope import decode_error_payload, decode_json, error_code_from_payload, require_data_dict
# SSE 类型的规范归属是 `.sse`；此处显式再导出，既有消费者（inbound/测试）导入路径不变
from .sse import SseEvent as SseEvent
from .sse import iter_sse_events as iter_sse_events

logger = logging.getLogger(__name__)

RUNS_PATH = "/v1/runs"
CONVERSATIONS_PATH = "/v1/conversations"
CANCEL_ACTIVE_PATH = "/v1/runs/cancel-active"
REQUEST_TIMEOUT_SEC = 10.0
STREAM_TIMEOUT_SEC = 300.0
CALLER_SERVICE = "muad-im-gateway"


class RuntimeClientPort(Protocol):
    def create_run(
        self,
        request: RunRequest,
        *,
        tenant_id: str,
        trace_id: str = "",
    ) -> AsyncIterator[SseEvent]: ...

    async def create_conversation(
        self,
        agent_id: UUID,
        platform_user_id: UUID,
        *,
        tenant_id: str = "",
        trace_id: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]: ...

    async def cancel_active(
        self,
        agent_id: UUID,
        platform_user_id: UUID,
        *,
        tenant_id: str = "",
        trace_id: str = "",
    ) -> dict[str, Any]: ...


def build_headers(
    tenant_id: str,
    trace_id: str,
    *,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    """链路头 + 稳定幂等键（设计 API-06：Gateway 传 channel message id）。"""
    headers = {"X-Caller-Service": CALLER_SERVICE}
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    if trace_id:
        headers["X-Trace-Id"] = trace_id
    request_id = current_request_id()
    if request_id:
        headers["X-Request-Id"] = request_id
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers



class RuntimeClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_sec: float = REQUEST_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_sec, transport=transport)

    async def create_run(
        self,
        request: RunRequest,
        *,
        tenant_id: str,
        trace_id: str = "",
        idempotency_key: str | None = None,
    ) -> AsyncIterator[SseEvent]:
        # 稳定幂等键：默认使用原 channel message id（可重试提交不重复建 Run）
        headers = build_headers(
            tenant_id, trace_id, idempotency_key=idempotency_key or request.message.id
        )
        headers["Accept"] = "text/event-stream"
        try:
            async with self._client.stream(
                "POST",
                RUNS_PATH,
                json=request.model_dump(mode="json"),
                headers=headers,
                timeout=httpx.Timeout(STREAM_TIMEOUT_SEC, connect=REQUEST_TIMEOUT_SEC),
            ) as response:
                if response.status_code >= 400:
                    payload = await decode_error_payload(response)
                    raise AppError(error_code_from_payload(payload))
                # 分片文本（非行原子）：解析器自行切行，跨 chunk 的帧不丢
                async for event in iter_sse_events(response.aiter_text()):
                    yield event
        except httpx.HTTPError as exc:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc

    async def create_conversation(
        self,
        agent_id: UUID,
        platform_user_id: UUID,
        *,
        tenant_id: str = "",
        trace_id: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        payload = {"agent_id": str(agent_id), "platform_user_id": str(platform_user_id)}
        return await self._post_json(
            CONVERSATIONS_PATH, payload, tenant_id, trace_id, idempotency_key=idempotency_key
        )

    async def cancel_active(
        self,
        agent_id: UUID,
        platform_user_id: UUID,
        *,
        tenant_id: str = "",
        trace_id: str = "",
    ) -> dict[str, Any]:
        payload = {"agent_id": str(agent_id), "platform_user_id": str(platform_user_id)}
        return await self._post_json(CANCEL_ACTIVE_PATH, payload, tenant_id, trace_id)

    async def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        tenant_id: str,
        trace_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = build_headers(tenant_id, trace_id, idempotency_key=idempotency_key)
        try:
            response = await self._client.post(path, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
        body = decode_json(response)
        if response.status_code >= 400:
            raise AppError(error_code_from_payload(body))
        return require_data_dict(body)

    async def aclose(self) -> None:
        await self._client.aclose()
