from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx
from muad_contracts import DeliveryRequest

DELIVERY_PATH = "/internal/deliveries"


class DeliveryTransportError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Gateway 的投递结果。

    `delivered=False` 表示 Gateway 只占位、尚未确认送达（in-flight 重复）：
    Worker 不得据此置 SENT，应按可重试失败处理。
    """

    status_code: int
    delivered: bool = True


class DeliveryClientProtocol(Protocol):
    async def deliver(self, request: DeliveryRequest) -> DeliveryResult: ...


class HttpDeliveryClient:
    def __init__(self, base_url: str, client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        try:
            response = await self._client.post(
                f"{self._base_url}{DELIVERY_PATH}",
                json=request.model_dump(mode="json"),
            )
        except httpx.HTTPError as exc:
            raise DeliveryTransportError(str(exc)) from exc
        return DeliveryResult(status_code=response.status_code, delivered=_delivered(response))


def _delivered(response: httpx.Response) -> bool:
    """兼容旧响应：缺少 `delivered` 字段时按已送达处理。"""
    try:
        data = response.json().get("data") or {}
    except ValueError:
        return True
    value = data.get("delivered")
    return bool(value) if isinstance(value, bool) else True
