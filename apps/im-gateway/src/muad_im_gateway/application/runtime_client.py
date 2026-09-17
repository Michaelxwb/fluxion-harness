from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import httpx
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import RunRequest

from .envelope import decode_error_payload, decode_json, error_code_from_payload, require_data_dict

logger = logging.getLogger(__name__)

RUNS_PATH = "/v1/runs"
CONVERSATIONS_PATH = "/v1/conversations"
CANCEL_ACTIVE_PATH = "/v1/runs/cancel-active"
REQUEST_TIMEOUT_SEC = 10.0
STREAM_TIMEOUT_SEC = 300.0
CALLER_SERVICE = "muad-im-gateway"

KNOWN_EVENT_TYPES = frozenset(
    {
        "run.created",
        "message.delta",
        "skill.loaded",
        "tool.started",
        "tool.completed",
        "task.accepted",
        "artifact.created",
        "interrupt.required",
        "run.completed",
        "run.failed",
    }
)


@dataclass(frozen=True)
class SseEvent:
    type: str
    data: dict[str, Any]


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
    ) -> dict[str, Any]: ...

    async def cancel_active(
        self,
        agent_id: UUID,
        platform_user_id: UUID,
        *,
        tenant_id: str = "",
        trace_id: str = "",
    ) -> dict[str, Any]: ...


def build_headers(tenant_id: str, trace_id: str) -> dict[str, str]:
    headers = {"X-Caller-Service": CALLER_SERVICE}
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    if trace_id:
        headers["X-Trace-Id"] = trace_id
    return headers


def _flush_event(event_type: str, data_lines: list[str]) -> SseEvent | None:
    if event_type not in KNOWN_EVENT_TYPES or not data_lines:
        return None
    try:
        payload = json.loads("\n".join(data_lines))
    except ValueError:
        logger.warning("sse_invalid_json event_type=%s", event_type)
        return None
    if not isinstance(payload, dict):
        logger.warning("sse_non_object_data event_type=%s", event_type)
        return None
    return SseEvent(type=event_type, data=payload)


async def iter_sse_events(lines: AsyncIterator[str]) -> AsyncIterator[SseEvent]:
    event_type = ""
    data_lines: list[str] = []
    async for raw_line in lines:
        line = raw_line.rstrip("\r")
        if line.startswith(":"):
            continue
        if not line:
            event = _flush_event(event_type, data_lines)
            if event is not None:
                yield event
            event_type = ""
            data_lines = []
            continue
        field, _, value = line.partition(":")
        if field == "event":
            event_type = value.strip()
        elif field == "data":
            data_lines.append(value[1:] if value.startswith(" ") else value)
    event = _flush_event(event_type, data_lines)
    if event is not None:
        yield event


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
    ) -> AsyncIterator[SseEvent]:
        headers = build_headers(tenant_id, trace_id)
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
                async for event in iter_sse_events(response.aiter_lines()):
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
    ) -> dict[str, Any]:
        payload = {"agent_id": str(agent_id), "platform_user_id": str(platform_user_id)}
        return await self._post_json(CONVERSATIONS_PATH, payload, tenant_id, trace_id)

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
    ) -> dict[str, Any]:
        headers = build_headers(tenant_id, trace_id)
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
