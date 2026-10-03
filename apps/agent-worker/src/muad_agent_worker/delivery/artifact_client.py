"""产物引用解析客户端（TASK-006）：`artifact_id` → 渠道中立的 `AttachmentRef`。

**为什么后台任务需要一个解析调用**：worker 只持有**不透明的** `result_artifact_id`
（那是 `execution_outcomes` 的设计："只落引用 + 预览"），而交付要的引用需要存储键与元信息。
**解析口径只有 runtime 那一处**（`/internal/artifacts/{id}`）—— 让 worker 直读
`runtime.artifact` 是跨 schema 的表结构耦合：runtime 改一列，worker 的投递会**静默**发错形态。

这确实新增了一条 `worker → runtime` 调用边；代价换的是"引用怎么算"只有一处定义。
"""

from __future__ import annotations

import uuid

import httpx
from muad_contracts import AttachmentRef

RESOLVE_PATH = "/internal/artifacts"
RESOLVE_TIMEOUT_SEC = 5.0


class ArtifactGoneError(Exception):
    """产物不存在或不可访问（404）：**重试多少次都不会好**，调用方不该把它当可重试失败。"""


class ArtifactResolveError(Exception):
    """解析**没拿到结论**（超时/传输/5xx）：可重试。"""


class ArtifactResolveClient:
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

    async def aclose(self) -> None:
        await self._client.aclose()

    async def resolve(self, artifact_id: uuid.UUID, *, tenant_id: str) -> AttachmentRef:
        headers = {"X-Tenant-Id": tenant_id}
        if self._service_token:
            headers["X-Internal-Service"] = self._service_token
        try:
            response = await self._client.get(f"{RESOLVE_PATH}/{artifact_id}", headers=headers)
        except httpx.HTTPError as exc:
            raise ArtifactResolveError(type(exc).__name__) from exc
        if response.status_code == 404:
            raise ArtifactGoneError(str(artifact_id))
        if response.status_code >= 400:
            raise ArtifactResolveError(f"http_{response.status_code}")
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ArtifactResolveError("malformed_resolve_response")
        return AttachmentRef.model_validate(data)
