from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    ResolveDefinitionRequest,
    ResolveDefinitionResponse,
    ResolveModelRequest,
    ResolveModelResponse,
)

from ..application.ports import AdmissionMaterials, PlatformSettingsSnapshot

RESOLVE_DEFINITION_PATH = "/internal/runtime/resolve-definition"
ADMISSION_MATERIALS_PATH = "/internal/runtime/admission-materials"
RESOLVE_MODEL_PATH = "/internal/runtime/resolve-model"
RESOLVE_CREDENTIALS_PATH = "/internal/runtime/resolve-credentials"
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
        service_token: str | None = None,
        timeout_sec: float = RESOLVE_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._service_token = service_token
        # 请求用绝对 URL 组装：注入的共享池客户端没有 base_url（一个池服务多个目标）。
        self._base_url = base_url.rstrip("/")
        # 装配根注入的控制池由**装配根拥有并关闭**（本实例只借用）；未注入时自建自关。
        self._owns_client = client is None
        # 自建时按单一目标配 base_url（请求仍用绝对 URL，注入共享池时同样成立）。
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=timeout_sec, transport=transport
        )

    async def resolve(
        self,
        request: ResolveDefinitionRequest,
        *,
        tenant_id: str,
        trace_id: str = "",
    ) -> ResolveDefinitionResponse:
        headers = {"X-Tenant-Id": tenant_id}
        # resolve-definition 与 API-08/09 同门控（响应含模型明文 api_key）。
        if self._service_token:
            headers["X-Internal-Service"] = self._service_token
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        response = await self._client.post(
            f"{self._base_url}{RESOLVE_DEFINITION_PATH}",
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

    async def resolve_model(
        self,
        request: ResolveModelRequest,
        *,
        tenant_id: str,
        trace_id: str = "",
    ) -> ResolveModelResponse:
        """按既有模型定义的主键解析单个模型（摘要模型）；响应含明文 `api_key`，同门控。"""
        headers = {"X-Tenant-Id": tenant_id}
        if self._service_token:
            headers["X-Internal-Service"] = self._service_token
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        response = await self._client.post(
            f"{self._base_url}{RESOLVE_MODEL_PATH}",
            json=request.model_dump(mode="json"),
            headers=headers,
        )
        payload = self._decode(response)
        if response.status_code >= 400:
            raise AppError(error_code_from_payload(payload))
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise AppError(str(ErrorCode.COMMON_INTERNAL_ERROR))
        return ResolveModelResponse.model_validate(data)

    async def fetch_admission_materials(
        self,
        *,
        tenant_id: str,
        agent_id: UUID,
        actor_user_id: UUID,
        execution_id: UUID,
        channel: str | None = None,
        trace_id: str = "",
    ) -> AdmissionMaterials:
        """受理边界一次取齐定义、设置快照与当前认证值（API-06/API-09 合并）。

        响应含模型明文 `api_key`，与单点解析端点同门控；凭据只进内存。
        """
        headers = {"X-Tenant-Id": tenant_id}
        if self._service_token:
            headers["X-Internal-Service"] = self._service_token
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        body: dict[str, Any] = {
            "agent_id": str(agent_id),
            "actor_user_id": str(actor_user_id),
            "execution_id": str(execution_id),
        }
        if channel is not None:
            body["channel"] = channel
        response = await self._client.post(
            f"{self._base_url}{ADMISSION_MATERIALS_PATH}", json=body, headers=headers
        )
        payload = self._decode(response)
        if response.status_code >= 400:
            raise AppError(error_code_from_payload(payload))
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise AppError(str(ErrorCode.COMMON_INTERNAL_ERROR))
        definition = ResolveDefinitionResponse.model_validate(data.get("definition") or {})
        snapshot = PlatformSettingsSnapshot(
            revision=int(data.get("settings_revision") or 0),
            settings=data.get("settings") or {},
        )
        credentials = data.get("credentials") or {}
        api_key = (credentials.get("model") or {}).get("api_key")
        if not api_key:
            raise AppError(ErrorCode.CREDENTIAL_MISSING)
        secrets = {
            str(item.get("mcp_server_id")): str(item["auth_secret"])
            for item in credentials.get("mcp_servers") or []
            if item.get("auth_secret")
        }
        return AdmissionMaterials(
            definition=definition,
            settings=snapshot,
            model=definition.model.model_copy(update={"api_key": str(api_key)}),
            mcp_secrets=secrets,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class ConsoleCredentialsClient:
    """API-09 客户端：认证数据只进入内存对象，不落盘/日志。"""

    def __init__(
        self,
        base_url: str,
        *,
        service_token: str | None = None,
        timeout_sec: float = RESOLVE_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._service_token = service_token
        # 与 `ConsoleResolveClient` 同一口径：借用注入的控制池（装配根拥有并关闭），
        # 未注入时自建自关；请求用绝对 URL 组装。
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        # 自建时按单一目标配 base_url（请求仍用绝对 URL，注入共享池时同样成立）。
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=timeout_sec, transport=transport
        )

    async def resolve_credentials(
        self,
        *,
        tenant_id: str,
        payload: dict[str, Any],
        trace_id: str = "",
    ) -> dict[str, Any]:
        headers = {"X-Tenant-Id": tenant_id}
        if self._service_token:
            headers["X-Internal-Service"] = self._service_token
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        response = await self._client.post(
            f"{self._base_url}{RESOLVE_CREDENTIALS_PATH}", json=payload, headers=headers
        )
        payload_body = ConsoleResolveClient._decode(response)
        if response.status_code >= 400:
            raise AppError(error_code_from_payload(payload_body))
        data = payload_body.get("data") if isinstance(payload_body, dict) else None
        if not isinstance(data, dict):
            raise AppError(str(ErrorCode.COMMON_INTERNAL_ERROR))
        return data

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
