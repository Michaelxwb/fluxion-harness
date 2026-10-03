"""产物取件服务（设计 API-05）：鉴权 → 校验归属 → 从共享 store 取字节。

**渠道无关能力**：Console 页面、未来 web chat、以及"渠道不能直发文件"时的降级链接，
复用**同一个**鉴权端点与**同一套**令牌模型。本期只有两个消费方，但接口按多消费方设计。

**元信息从哪来**：`runtime.artifact` 是 **runtime** 的 Owner 表，而 console 只碰 `control`
（仓库里没有 console 跨 schema 读的先例）。所以归属校验走 **runtime 的
`GET /internal/artifacts/{artifact_id}`**（解析单点，与 worker 那条路径同源）——而不是让
console 去直读别人的表：那样 runtime 改一列，console 的取件会**静默**退化。
解析端点对"不存在/跨租户"一律 404，正好是这里要的语义。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from muad_artifact_store import NfsArtifactStore

#: runtime 的解析端点
RESOLVE_PATH = "/internal/artifacts"
RESOLVE_TIMEOUT_SEC = 5.0


@dataclass(frozen=True, slots=True)
class ArtifactFetchTarget:
    """取件目标：**存储路径 + 呈现元信息**。

    路径只在本模块与端点之间流转，**绝不进响应**——把 `storage_key` 或真实路径回给客户端，
    等于把共享存储的目录结构告诉对方。
    """

    path: Path
    media_type: str
    filename: str | None


class ArtifactResolvePort:
    """解析端口：调 runtime 的解析端点，把 `artifact_id` 换成渠道中立引用。"""

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

    async def resolve(self, artifact_id: uuid.UUID, *, tenant_id: str) -> dict[str, object] | None:
        """返回解析出的引用；**不存在 / 跨租户 / 解析不通 → `None`**（调用方据此统一 404）。

        解析不通与"不存在"对取件者是同一件事：都拿不到东西。**不区分**——区分就会泄露
        "这个 id 存在但你现在拿不到"。
        """
        headers = {"X-Tenant-Id": tenant_id}
        if self._service_token:
            headers["X-Internal-Service"] = self._service_token
        try:
            response = await self._client.get(f"{RESOLVE_PATH}/{artifact_id}", headers=headers)
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, dict) else None


class ArtifactFetchService:
    def __init__(self, *, store: NfsArtifactStore, resolver: ArtifactResolvePort) -> None:
        self._store = store
        self._resolver = resolver

    async def aclose(self) -> None:
        await self._resolver.aclose()

    async def fetch(self, artifact_id: uuid.UUID, *, tenant_id: str) -> ArtifactFetchTarget | None:
        """取件。**任何一步不成立都返回 `None`**，由端点统一映射成 404（不泄露存在性）。"""
        reference = await self._resolver.resolve(artifact_id, tenant_id=tenant_id)
        if reference is None:
            return None
        storage_key = reference.get("storage_key")
        media_type = reference.get("media_type")
        if not isinstance(storage_key, str) or not storage_key:
            return None
        if not isinstance(media_type, str) or not media_type:
            return None
        try:
            path = self._store.resolve(storage_key)
        except ValueError:  # storage_key 越界/非法：当作不存在
            return None
        if not path.is_file():
            # 行在、字节没了（清理过、盘掉过）：对取件者同样是"没有"。留痕由清理侧负责。
            return None
        filename = reference.get("filename")
        return ArtifactFetchTarget(
            path=path,
            media_type=media_type,
            filename=filename if isinstance(filename, str) and filename else None,
        )
