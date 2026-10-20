"""内部取平台设置快照客户端（design §3.2/§3.4 API-06；TASK-005）。

与 `ConsoleResolveClient` 同一条内部 HTTP 通道与服务身份门控（`X-Internal-Service`），
差别只有两点：走 `GET /internal/v1/platform-settings`，并带 `X-Caller-Service: runtime`
（让 Console 侧的 `platform_settings_fetch_total{caller=...}` 标签可辨）。

**失败即失败**：端点不可达或返回错误一律抛 `AppError`，并把失败记在**调用方**侧
——Console 端点被切断时 Console 根本收不到请求，`result="failed"` 只能由这里记（RULE-06）。
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from muad_api import AppError, inc_counter
from muad_api.error_codes import ErrorCode

from ..application.ports import PlatformSettingsSnapshot
from ..metrics import PLATFORM_SETTINGS_FETCH_METRIC
from .console_client import error_code_from_payload

PLATFORM_SETTINGS_PATH = "/internal/v1/platform-settings"
PLATFORM_SETTINGS_TIMEOUT_SEC = 5.0
#: 设置快照的进程内缓存时长：Run 创建是热路径，每单同步 HTTP 取一次会把准入锁在
#: ~30ms 往返上（2026-10-20 实测 ~34 次/秒）。设置变更在 TTL 内对**新 Run** 生效；
#: 检测到策略行版本更高时调用方用 `force=True` 绕过缓存立即刷新。
PLATFORM_SETTINGS_CACHE_TTL_SEC = 1.0
RUNTIME_CALLER = "runtime"


class ConsolePlatformSettingsClient:
    def __init__(
        self,
        base_url: str,
        *,
        service_token: str | None = None,
        caller: str = RUNTIME_CALLER,
        timeout_sec: float = PLATFORM_SETTINGS_TIMEOUT_SEC,
        cache_ttl_sec: float = PLATFORM_SETTINGS_CACHE_TTL_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._caller = caller
        self._cache_ttl_sec = max(cache_ttl_sec, 0.0)
        self._cache: dict[str, tuple[float, PlatformSettingsSnapshot]] = {}
        self._service_token = service_token
        # 请求用绝对 URL 组装：注入的共享池客户端没有 base_url（一个池服务多个目标）。
        self._base_url = base_url.rstrip("/")
        # 装配根注入的控制池由**装配根拥有并关闭**（本实例只借用）；未注入时自建自关。
        self._owns_client = client is None
        # 自建时按单一目标配 base_url（请求仍用绝对 URL，注入共享池时同样成立）。
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=timeout_sec, transport=transport
        )

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = "", force: bool = False
    ) -> PlatformSettingsSnapshot:
        now = time.monotonic()
        if not force and self._cache_ttl_sec > 0:
            hit = self._cache.get(tenant_id)
            if hit is not None and hit[0] > now:
                return hit[1]
        headers = {"X-Tenant-Id": tenant_id, "X-Caller-Service": self._caller}
        if self._service_token:
            headers["X-Internal-Service"] = self._service_token
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        try:
            response = await self._client.get(
                f"{self._base_url}{PLATFORM_SETTINGS_PATH}", headers=headers
            )
        except httpx.HTTPError as exc:
            self._record_failed()
            raise AppError(str(ErrorCode.COMMON_INTERNAL_ERROR)) from exc
        payload = self._decode(response)
        if response.status_code >= 400:
            self._record_failed()
            raise AppError(error_code_from_payload(payload))
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("settings"), dict):
            self._record_failed()
            raise AppError(str(ErrorCode.COMMON_INTERNAL_ERROR))
        revision = data.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            self._record_failed()
            raise AppError(str(ErrorCode.COMMON_INTERNAL_ERROR))
        snapshot = PlatformSettingsSnapshot(revision=revision, settings=data["settings"])
        if self._cache_ttl_sec > 0:
            self._cache[tenant_id] = (now + self._cache_ttl_sec, snapshot)
        return snapshot

    def _record_failed(self) -> None:
        inc_counter(PLATFORM_SETTINGS_FETCH_METRIC, 1, {"caller": self._caller, "result": "failed"})

    @staticmethod
    def _decode(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
