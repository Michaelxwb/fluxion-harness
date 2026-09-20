"""Runtime Egress 客户端：调用 Console API-08 获取平台解析与凭据（内存使用）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from muad_api import AppError
from muad_api.error_codes import ErrorCode

RESOLVE_EGRESS_PATH = "/internal/runtime/resolve-egress-access"


def error_code_from_payload(payload: Any) -> str:
    if isinstance(payload, dict):
        code = payload.get("code")
        if isinstance(code, str) and code:
            return code
    return str(ErrorCode.COMMON_INTERNAL_ERROR)


class TransportClient(Protocol):
    async def post(self, path: str, json: dict, headers: dict) -> Any: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class EgressCredential:
    ref_id: str | None
    credential_json: dict[str, Any] | None
    credential_schema_version: str | None


@dataclass(frozen=True, slots=True)
class EgressPlatform:
    id: str
    key: str
    resolver_type: str
    resolver_config: dict[str, Any]
    adapter_key: str
    adapter_config: dict[str, Any]
    adapter_schema_version: str
    credential_mode: str


@dataclass(frozen=True, slots=True)
class EgressAccess:
    decision: str
    platform: EgressPlatform
    credential: EgressCredential | None


class RuntimeEgressClient:
    """Egress Boundary 的 Console 侧解析客户端；凭据仅内存使用，不落盘/日志。"""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_sec: float = 5.0,
        client: TransportClient | None = None,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_sec)

    async def resolve(self, *, tenant_id: str, payload: dict[str, Any], trace_id: str = "") -> EgressAccess:
        headers = {"X-Tenant-Id": tenant_id}
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        response = await self._client.post(RESOLVE_EGRESS_PATH, json=payload, headers=headers)
        body = response.json()
        if response.status_code >= 400:
            raise AppError(error_code_from_payload(body))
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise AppError(str(ErrorCode.COMMON_INTERNAL_ERROR))
        platform_data = data.get("platform") or {}
        credential_data = data.get("credential")
        return EgressAccess(
            decision=str(data.get("decision", "ALLOW")),
            platform=EgressPlatform(
                id=str(platform_data.get("id")),
                key=str(platform_data.get("key")),
                resolver_type=str(platform_data.get("resolver_type")),
                resolver_config=dict(platform_data.get("resolver_config") or {}),
                adapter_key=str(platform_data.get("adapter_key")),
                adapter_config=dict(platform_data.get("adapter_config") or {}),
                adapter_schema_version=str(platform_data.get("adapter_schema_version", "1")),
                credential_mode=str(platform_data.get("credential_mode")),
            ),
            credential=(
                None
                if credential_data is None
                else EgressCredential(
                    ref_id=credential_data.get("ref_id"),
                    credential_json=credential_data.get("credential_json") or {},
                    credential_schema_version=str(
                        credential_data.get("credential_schema_version", "1")
                    ),
                )
            ),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
