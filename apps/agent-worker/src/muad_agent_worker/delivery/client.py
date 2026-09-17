from __future__ import annotations

from typing import Protocol

import httpx
from muad_contracts import DeliveryRequest

DELIVERY_PATH = "/internal/deliveries"


class DeliveryTransportError(Exception):
    pass


class DeliveryClientProtocol(Protocol):
    async def deliver(self, request: DeliveryRequest) -> int: ...


class HttpDeliveryClient:
    def __init__(self, base_url: str, client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def deliver(self, request: DeliveryRequest) -> int:
        try:
            response = await self._client.post(
                f"{self._base_url}{DELIVERY_PATH}",
                json=request.model_dump(mode="json"),
            )
        except httpx.HTTPError as exc:
            raise DeliveryTransportError(str(exc)) from exc
        return response.status_code
