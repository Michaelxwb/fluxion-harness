"""API-09（Console 内部面）：按**冻结引用**解析当前模型/MCP 认证值。

- 密钥只进调用内存：本客户端不落盘、不写日志，解析结果由模型 Provider 直接持有；
- 归属由 Console 按 **Execution 行**复核（tenant → actor → 冻结模型）：请求体不是信任来源，
  我们只发服务身份 + 租户/操作者头；
- `MODEL_CREDENTIAL_CHANGED` 是显式结论（冻结引用与当前事实不符）：调用方必须停手重新受理，
  不能拿新 Key 消费旧槽位。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from muad_contracts import ResolveCredentialsRequest, RuntimeExecutionRef

RESOLVE_CREDENTIALS_PATH = "/internal/runtime/resolve-credentials"
DEFAULT_TIMEOUT_SEC = 5.0


class CredentialResolutionError(Exception):
    """凭据不可解析：`code` 是 Console 的稳定错误码（无结论时 `CREDENTIAL_UNAVAILABLE`）。"""

    def __init__(self, message: str, *, code: str = "CREDENTIAL_UNAVAILABLE") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ResolvedModelCredential:
    model_id: UUID
    deployment_id: UUID
    credential_id: UUID
    credential_revision: int
    key_resource_id: UUID
    quota_group_id: UUID | None
    provider_key: str
    base_url: str
    api_key: str


@dataclass(frozen=True, slots=True)
class ResolvedCredentials:
    model: ResolvedModelCredential
    #: MCP server id → 认证值（`None` 表示该 server 无认证）。
    mcp_secrets: dict[UUID, str | None]


class ConsoleCredentialClient:
    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient,
        *,
        service_token: str | None = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._service_token = service_token
        self._timeout_sec = timeout_sec

    async def resolve(
        self,
        *,
        tenant_id: str,
        actor_user_id: UUID,
        execution_id: UUID,
        model_id: UUID,
        deployment_id: UUID | None = None,
        credential_id: UUID | None = None,
        expected_revision: int | None = None,
        mcp_server_ids: tuple[UUID, ...] = (),
    ) -> ResolvedCredentials:
        payload = ResolveCredentialsRequest(
            execution_ref=RuntimeExecutionRef(type="TASK", id=execution_id),
            model_id=model_id,
            actor_user_id=actor_user_id,
            deployment_id=deployment_id,
            credential_id=credential_id,
            expected_revision=expected_revision,
            mcp_server_ids=list(mcp_server_ids),
        )
        body = await self._post(tenant_id, payload.model_dump(mode="json"))
        return _parse(body)

    async def _post(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"X-Tenant-Id": tenant_id}
        if self._service_token:
            headers["X-Internal-Service"] = self._service_token
        try:
            response = await self._client.post(
                f"{self._base_url}{RESOLVE_CREDENTIALS_PATH}",
                json=payload,
                headers=headers,
                timeout=self._timeout_sec,
            )
        except httpx.HTTPError as exc:
            raise CredentialResolutionError(type(exc).__name__) from exc
        body = _envelope(response)
        code = body.get("code")
        if code != "0":
            raise CredentialResolutionError(str(code), code=str(code))
        data = body.get("data")
        if not isinstance(data, dict):
            raise CredentialResolutionError("malformed_credentials_response")
        return data


def _envelope(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise CredentialResolutionError(f"http_{response.status_code}") from exc
    if not isinstance(body, dict):
        raise CredentialResolutionError(f"http_{response.status_code}")
    return body


def _parse(data: dict[str, Any]) -> ResolvedCredentials:
    model = data.get("model")
    if not isinstance(model, dict):
        raise CredentialResolutionError("malformed_credentials_response")
    try:
        resolved = ResolvedModelCredential(
            model_id=UUID(str(model["model_id"])),
            deployment_id=UUID(str(model["deployment_id"])),
            credential_id=UUID(str(model["credential_id"])),
            credential_revision=int(model["credential_revision"]),
            key_resource_id=UUID(str(model["key_resource_id"])),
            quota_group_id=(
                UUID(str(model["quota_group_id"])) if model.get("quota_group_id") else None
            ),
            provider_key=str(model["provider_key"]),
            base_url=str(model["base_url"]),
            api_key=str(model["api_key"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CredentialResolutionError("malformed_credentials_response") from exc
    secrets: dict[UUID, str | None] = {}
    for entry in data.get("mcp_servers") or ():
        if not isinstance(entry, dict):
            raise CredentialResolutionError("malformed_credentials_response")
        secret = entry.get("auth_secret")
        secrets[UUID(str(entry["mcp_server_id"]))] = secret if isinstance(secret, str) else None
    return ResolvedCredentials(model=resolved, mcp_secrets=secrets)


__all__ = [
    "ConsoleCredentialClient",
    "CredentialResolutionError",
    "ResolvedCredentials",
    "ResolvedModelCredential",
]
