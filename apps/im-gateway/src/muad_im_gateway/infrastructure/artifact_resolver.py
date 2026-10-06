"""产物归属解析客户端：`artifact_id` → **渠道中立的** `AttachmentRef`（含权威 `storage_key`）。

**网关为什么要自己解析一遍**：`POST /internal/deliveries` 的调用方（Runtime / Worker）在请求体
里自带 `storage_key`，而网关**按它直读共享存储**。不校验就等于"内网里谁说读哪个文件就读哪个"
——2026-10-06 实测：不带任何凭据声明 `tenant_id=attacker-tenant` + 一个受害租户的存储键，
网关照样把那份字节上传给了用户。

解析口径**只有 Runtime 那一处**（与 Console 取件、Worker 后台投递同一个端点，见
`apps/agent-runtime/src/muad_agent_runtime/api/artifacts.py`）：跨租户/不存在一律 404，
且不泄露存在性。网关不再自己拼一份"看起来像"的存储键。

**两类失败分开**：`ArtifactGoneError`（404——重试不会变好）与 `ArtifactResolveError`
（没拿到结论——可重试）。混淆二者会让"产物真的没了"被无限重试，或让一次网络抖动被当成终态。
"""

from __future__ import annotations

import logging
import uuid
from typing import Protocol

import httpx
from muad_api.security import INTERNAL_SERVICE_HEADER
from muad_contracts import AttachmentRef

logger = logging.getLogger(__name__)

RESOLVE_PATH = "/internal/artifacts"
RESOLVE_TIMEOUT_SEC = 5.0


class ArtifactGoneError(Exception):
    """产物不存在 / 不属于本租户 / 与调用方声明的存储键不符。**重试不会变好。**"""


class ArtifactResolveError(Exception):
    """解析**没拿到结论**（超时、传输错误、5xx、坏封套）：可重试。"""


class ArtifactResolverPort(Protocol):
    async def resolve(self, artifact_id: uuid.UUID, *, tenant_id: str) -> AttachmentRef: ...
    async def aclose(self) -> None: ...


class HttpArtifactResolver:
    """真实 HTTP 实现。租户经 `X-Tenant-Id` 显式声明（内部端点没有用户主体）。"""

    def __init__(
        self,
        base_url: str,
        *,
        service_token: str | None = None,
        timeout_sec: float = RESOLVE_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._service_token = service_token
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_sec, transport=transport)

    async def resolve(self, artifact_id: uuid.UUID, *, tenant_id: str) -> AttachmentRef:
        headers = {"X-Tenant-Id": tenant_id}
        if self._service_token:
            headers[INTERNAL_SERVICE_HEADER] = self._service_token
        try:
            response = await self._client.get(f"{RESOLVE_PATH}/{artifact_id}", headers=headers)
        except httpx.HTTPError as exc:
            raise ArtifactResolveError(type(exc).__name__) from exc
        if response.status_code == 404:
            raise ArtifactGoneError(str(artifact_id))
        if response.status_code >= 400:
            raise ArtifactResolveError(f"http_{response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ArtifactResolveError("malformed_resolve_response") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ArtifactResolveError("malformed_resolve_response")
        return AttachmentRef.model_validate(data)

    async def aclose(self) -> None:
        await self._client.aclose()
