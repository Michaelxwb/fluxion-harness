from __future__ import annotations

import logging
from typing import Any, Protocol, TypeVar
from uuid import UUID

import httpx
from muad_api import AppError
from muad_api.context import current_request_id, current_trace_id
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    DEFAULT_PAGE_SIZE,
    BotSnapshotResponse,
    ChannelBindRequest,
    ChannelBindResponse,
    ChannelResolveRequest,
    ChannelResolveResponse,
    ChannelSkillsResponse,
)
from pydantic import BaseModel

from .envelope import decode_json, error_code_from_payload, require_data_model
from .runtime_client import CALLER_SERVICE

logger = logging.getLogger(__name__)

ContractT = TypeVar("ContractT", bound=BaseModel)

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
        *,
        idempotency_key: str | None = None,
    ) -> ChannelBindResponse: ...

    async def bots(
        self,
        tenant_id: str,
        *,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> BotSnapshotResponse: ...

    async def channel_skills(
        self,
        agent_id: UUID,
        platform_user_id: UUID,
        tenant_id: str,
        *,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> ChannelSkillsResponse: ...


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
        return await self._request_model(
            "POST",
            RESOLVE_PATH,
            ChannelResolveResponse,
            tenant_id,
            json_body=request.model_dump(mode="json"),
        )

    async def bind(
        self,
        request: ChannelBindRequest,
        tenant_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> ChannelBindResponse:
        return await self._request_model(
            "POST",
            BIND_PATH,
            ChannelBindResponse,
            tenant_id,
            json_body=request.model_dump(mode="json"),
            headers=_idempotency_headers(idempotency_key),
        )

    async def bots(
        self,
        tenant_id: str,
        *,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> BotSnapshotResponse:
        params = {"page": str(page), "page_size": str(page_size)}
        return await self._request_model(
            "GET", BOTS_PATH, BotSnapshotResponse, tenant_id, params=params
        )

    async def channel_skills(
        self,
        agent_id: UUID,
        platform_user_id: UUID,
        tenant_id: str,
        *,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> ChannelSkillsResponse:
        params = {
            "agent_id": str(agent_id),
            "platform_user_id": str(platform_user_id),
            "page": str(page),
            "page_size": str(page_size),
        }
        return await self._request_model(
            "GET",
            CHANNEL_SKILLS_PATH,
            ChannelSkillsResponse,
            tenant_id,
            params=params,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request_model(
        self,
        method: str,
        path: str,
        model: type[ContractT],
        tenant_id: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ContractT:
        request_headers = {**_headers(tenant_id), **(headers or {})}
        try:
            response = await self._client.request(
                method,
                path,
                json=json_body,
                params=params,
                headers=request_headers,
            )
        except httpx.HTTPError as exc:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
        if response.status_code >= 400:
            raise AppError(error_code_from_payload(decode_json(response)))
        return require_data_model(response, model)


def _headers(tenant_id: str) -> dict[str, str]:
    headers = {"X-Caller-Service": CALLER_SERVICE}
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    trace_id = current_trace_id()
    if trace_id:
        headers["X-Trace-Id"] = trace_id
    request_id = current_request_id()
    if request_id:
        headers["X-Request-Id"] = request_id
    return headers


def _idempotency_headers(idempotency_key: str | None) -> dict[str, str]:
    """稳定幂等键：Gateway 传原 channel message_id（设计 API-03 Header 契约）。"""
    return {"Idempotency-Key": idempotency_key} if idempotency_key else {}
