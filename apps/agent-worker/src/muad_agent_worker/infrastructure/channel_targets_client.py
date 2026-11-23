"""Console-side bound IM targets for schedule delivery.

Delivery targets are identities the user has actually bound (for example a
direct-message identity), not historical ``task.delivery_route`` snapshots.
The channel set comes from ``OUTBOUND_CHANNELS``; adding another IM channel
only extends the contract/adapters and needs no branch here.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from muad_contracts import DeliveryRouteInput
from muad_contracts.enums import OUTBOUND_CHANNELS
from muad_logging import get_logger

logger = get_logger(__name__)

CHANNEL_TARGETS_PATH = "/internal/channel/targets"
CHANNEL_TARGETS_TIMEOUT_SEC = 5.0


class ConsoleChannelTargetsClient:
    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient,
        *,
        service_token: str | None = None,
        timeout_sec: float = CHANNEL_TARGETS_TIMEOUT_SEC,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._service_token = service_token
        self._timeout = timeout_sec

    async def list_targets(
        self,
        *,
        tenant_id: str,
        platform_user_id: uuid.UUID,
        agent_id: uuid.UUID | None,
    ) -> tuple[DeliveryRouteInput, ...]:
        headers = {"X-Tenant-Id": tenant_id}
        if self._service_token:
            headers["X-Internal-Service"] = self._service_token
        params: dict[str, str] = {"platform_user_id": str(platform_user_id)}
        if agent_id is not None:
            params["agent_id"] = str(agent_id)
        try:
            response = await self._client.get(
                f"{self._base_url}{CHANNEL_TARGETS_PATH}",
                headers=headers,
                params=params,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            logger.warning("channel_targets_unavailable error=%s", type(exc).__name__)
            return ()
        if response.status_code >= 400:
            logger.warning("channel_targets_http_error status=%s", response.status_code)
            return ()
        try:
            body: Any = response.json()
        except ValueError:
            logger.warning("channel_targets_invalid_json")
            return ()
        data = body.get("data") if isinstance(body, dict) else None
        raw_items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(raw_items, list):
            logger.warning("channel_targets_invalid_payload")
            return ()
        targets: list[DeliveryRouteInput] = []
        seen: set[tuple[str, str, str, str | None]] = set()
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            try:
                route = DeliveryRouteInput.model_validate(
                    {
                        "channel": item.get("channel"),
                        "bot_id": item.get("bot_id"),
                        "external_user_id": item.get("external_user_id"),
                        "external_conversation_id": item.get("external_conversation_id"),
                    }
                )
            except ValueError:
                continue
            if route.channel not in OUTBOUND_CHANNELS:
                continue
            key = (
                route.channel,
                route.bot_id,
                route.external_user_id,
                route.external_conversation_id,
            )
            if key in seen:
                continue
            seen.add(key)
            targets.append(route)
        return tuple(targets)
