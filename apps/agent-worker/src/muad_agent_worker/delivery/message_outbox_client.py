"""Worker → Runtime 的消息级投递客户端（design §4.6）。

`task.message_outbox` 的消费者只把**已冻结**的消息事实交给 Runtime；HTTP 2xx 且封套
`code=="0"` 才算受理。业务码（路由失效/附件越权/owner 不存在）与传输失败分开：
前者是终态拒绝，后者可重试——不能把“没连上”当成“发不出去”。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from muad_api.security import INTERNAL_SERVICE_HEADER
from muad_contracts import MessageDeliveryEnqueueReceipt, MessageDeliveryEnqueueRequest

MESSAGE_DELIVERIES_PATH = "/internal/message-deliveries"
SUCCESS_CODE = "0"


class MessageOutboxTransportError(Exception):
    """没有得出结论（网络/超时/非法响应）：调用方按可重试处理。"""


class MessageOutboxRejected(Exception):
    """Runtime 明确拒绝：`retryable=False` 时 outbox 置 FAILED。"""

    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class MessageOutboxReceipt:
    delivery_id: uuid.UUID
    status: str
    duplicate: bool


class MessageOutboxClientProtocol(Protocol):
    async def enqueue(
        self, *, tenant_id: str, request: MessageDeliveryEnqueueRequest
    ) -> MessageOutboxReceipt: ...


class HttpMessageOutboxClient:
    def __init__(
        self, base_url: str, client: httpx.AsyncClient, *, service_token: str | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._service_token = service_token

    async def enqueue(
        self, *, tenant_id: str, request: MessageDeliveryEnqueueRequest
    ) -> MessageOutboxReceipt:
        headers = {"X-Tenant-Id": tenant_id}
        if self._service_token:
            headers[INTERNAL_SERVICE_HEADER] = self._service_token
        try:
            response = await self._client.post(
                f"{self._base_url}{MESSAGE_DELIVERIES_PATH}",
                json=request.model_dump(mode="json"),
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise MessageOutboxTransportError(type(exc).__name__) from exc
        payload = _json_object(response)
        code = payload.get("code") if payload is not None else None
        if code != SUCCESS_CODE:
            raise MessageOutboxRejected(
                str(code or f"http_{response.status_code}"),
                retryable=_retryable_status(response.status_code),
            )
        data = payload.get("data") if payload is not None else None
        if not isinstance(data, dict):
            raise MessageOutboxTransportError("malformed_message_delivery_response")
        try:
            receipt = MessageDeliveryEnqueueReceipt.model_validate(data)
        except ValueError as exc:
            raise MessageOutboxTransportError("malformed_message_delivery_response") from exc
        return MessageOutboxReceipt(
            delivery_id=receipt.delivery_id,
            status=receipt.status,
            duplicate=receipt.duplicate,
        )


def _retryable_status(status_code: int) -> bool:
    return status_code >= 500 or status_code == 429


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


__all__ = [
    "MESSAGE_DELIVERIES_PATH",
    "HttpMessageOutboxClient",
    "MessageOutboxClientProtocol",
    "MessageOutboxReceipt",
    "MessageOutboxRejected",
    "MessageOutboxTransportError",
]
