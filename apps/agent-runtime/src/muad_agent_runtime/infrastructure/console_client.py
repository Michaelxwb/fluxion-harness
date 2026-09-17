from __future__ import annotations

from typing import Any

import httpx
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import ResolveDefinitionRequest, ResolveDefinitionResponse

RESOLVE_DEFINITION_PATH = "/internal/runtime/resolve-definition"
RESOLVE_TIMEOUT_SEC = 5.0


def error_code_from_payload(payload: Any) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str) and code:
                return code
        code = payload.get("code")
        if isinstance(code, str) and code:
            return code
    return str(ErrorCode.COMMON_INTERNAL_ERROR)


class ConsoleResolveClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_sec: float = RESOLVE_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_sec, transport=transport)

    async def resolve(
        self,
        request: ResolveDefinitionRequest,
        *,
        tenant_id: str,
        trace_id: str = "",
    ) -> ResolveDefinitionResponse:
        headers = {"X-Tenant-Id": tenant_id}
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        response = await self._client.post(
            RESOLVE_DEFINITION_PATH,
            json=request.model_dump(mode="json"),
            headers=headers,
        )
        payload = self._decode(response)
        if response.status_code >= 400:
            raise AppError(error_code_from_payload(payload))
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise AppError(str(ErrorCode.COMMON_INTERNAL_ERROR))
        return ResolveDefinitionResponse.model_validate(data)

    @staticmethod
    def _decode(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    async def aclose(self) -> None:
        await self._client.aclose()
