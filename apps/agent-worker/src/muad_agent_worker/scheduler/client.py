from __future__ import annotations

from typing import Protocol
from uuid import UUID

import httpx
from muad_contracts import ResolveDefinitionRequest, ResolveDefinitionResponse

RESOLVE_DEFINITION_PATH = "/internal/runtime/resolve-definition"


class ResolveTransportError(Exception):
    pass


class ResolveDefinitionProtocol(Protocol):
    async def resolve(
        self, agent_id: UUID, actor_user_id: UUID, tenant_id: str
    ) -> ResolveDefinitionResponse: ...


class ConsoleResolveClient:
    def __init__(self, base_url: str, client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def resolve(
        self, agent_id: UUID, actor_user_id: UUID, tenant_id: str
    ) -> ResolveDefinitionResponse:
        request = ResolveDefinitionRequest(
            agent_id=agent_id,
            actor_user_id=actor_user_id,
            channel="WECOM",
        )
        try:
            response = await self._client.post(
                f"{self._base_url}{RESOLVE_DEFINITION_PATH}",
                json=request.model_dump(mode="json"),
                headers={"X-Tenant-Id": tenant_id},
            )
        except httpx.HTTPError as exc:
            raise ResolveTransportError(str(exc)) from exc
        if response.status_code >= 400:
            raise ResolveTransportError(f"resolve-definition returned status {response.status_code}")
        body = response.json()
        return ResolveDefinitionResponse.model_validate(body["data"])
