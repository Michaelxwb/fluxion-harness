from __future__ import annotations

import logging
from typing import Any, Protocol
from uuid import UUID

import httpx
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    BotSnapshotResponse,
    ChannelBindRequest,
    ChannelBindResponse,
    ChannelResolveRequest,
    ChannelResolveResponse,
)

from .envelope import (
    decode_json,
    error_code_from_payload,
    require_data_dict,
    require_data_list,
)
from .runtime_client import CALLER_SERVICE

logger = logging.getLogger(__name__)

RESOLVE_PATH = "/internal/channel/resolve"
BIND_PATH = "/internal/channel/bind"
BOTS_PATH = "/internal/channel/bots"
CHANNEL_SKILLS_PATH = "/internal/channel/skills"
REQUEST_TIMEOUT_SEC = 5.0


class ConsoleClientPort(Protocol):
    async def resolve(
        self,
        request: ChannelResolveRequest,
        tenant_id: str,
    ) -> ChannelResolveResponse: ...

    async def bind(
        self,
        request: ChannelBindRequest,
        tenant_id: str,
    ) -> ChannelBindResponse: ...

    async def bots(self, tenant_id: str) -> BotSnapshotResponse: ...

    async def channel_skills(
        self,
        agent_id: UUID,
        platform_user_id: UUID,
        tenant_id: str,
    ) -> list[dict[str, Any]]: ...


class ConsoleClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_sec: float = REQUEST_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_sec, transport=transport)

    async def resolve(
        self,
        request: ChannelResolveRequest,
        tenant_id: str,
    ) -> ChannelResolveResponse:
        payload = await self._post_json(RESOLVE_PATH, request.model_dump(mode="json"), tenant_id)
        return ChannelResolveResponse.model_validate(payload)

    async def bind(
        self,
        request: ChannelBindRequest,
        tenant_id: str,
    ) -> ChannelBindResponse:
        payload = await self._post_json(BIND_PATH, request.model_dump(mode="json"), tenant_id)
        return ChannelBindResponse.model_validate(payload)

    async def bots(self, tenant_id: str) -> BotSnapshotResponse:
        payload = await self._get_json(BOTS_PATH, tenant_id, params=None)
        return BotSnapshotResponse.model_validate(payload)

    async def channel_skills(
        self,
        agent_id: UUID,
        platform_user_id: UUID,
        tenant_id: str,
    ) -> list[dict[str, Any]]:
        params = {"agent_id": str(agent_id), "platform_user_id": str(platform_user_id)}
        try:
            response = await self._client.get(
                CHANNEL_SKILLS_PATH,
                params=params,
                headers=_headers(tenant_id),
            )
        except httpx.HTTPError as exc:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
        if response.status_code == 404:
            logger.warning("channel_skills_not_found agent_id=%s", agent_id)
            return []
        body = decode_json(response)
        if response.status_code >= 400:
            raise AppError(error_code_from_payload(body))
        return [item for item in require_data_list(body) if isinstance(item, dict)]

    async def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        tenant_id: str,
    ) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=payload, headers=_headers(tenant_id))
        except httpx.HTTPError as exc:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
        body = decode_json(response)
        if response.status_code >= 400:
            raise AppError(error_code_from_payload(body))
        return require_data_dict(body)

    async def _get_json(
        self,
        path: str,
        tenant_id: str,
        *,
        params: dict[str, str] | None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.get(path, params=params, headers=_headers(tenant_id))
        except httpx.HTTPError as exc:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
        body = decode_json(response)
        if response.status_code >= 400:
            raise AppError(error_code_from_payload(body))
        return require_data_dict(body)

    async def aclose(self) -> None:
        await self._client.aclose()


def _headers(tenant_id: str) -> dict[str, str]:
    headers = {"X-Caller-Service": CALLER_SERVICE}
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    return headers
