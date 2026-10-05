"""内部取平台设置快照客户端（design §3.2/§3.4 API-06；TASK-005）。

与 `ConsoleResolveClient` 同一条内部 HTTP 通道与服务身份门控（`X-Internal-Service`），
差别只有两点：走 `GET /internal/v1/platform-settings`，并带 `X-Caller-Service: runtime`
（让 Console 侧的 `platform_settings_fetch_total{caller=...}` 标签可辨）。

**失败即失败**：端点不可达或返回错误一律抛 `AppError`，并把失败记在**调用方**侧
——Console 端点被切断时 Console 根本收不到请求，`result="failed"` 只能由这里记（RULE-06）。
"""

from __future__ import annotations

from typing import Any

import httpx
from muad_api import AppError, inc_counter
from muad_api.error_codes import ErrorCode

from ..application.ports import PlatformSettingsSnapshot
from ..metrics import PLATFORM_SETTINGS_FETCH_METRIC
from .console_client import error_code_from_payload

PLATFORM_SETTINGS_PATH = "/internal/v1/platform-settings"
PLATFORM_SETTINGS_TIMEOUT_SEC = 5.0
RUNTIME_CALLER = "runtime"


class ConsolePlatformSettingsClient:
    def __init__(
        self,
        base_url: str,
        *,
        service_token: str | None = None,
        caller: str = RUNTIME_CALLER,
        timeout_sec: float = PLATFORM_SETTINGS_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._caller = caller
        self._service_token = service_token
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_sec, transport=transport)

    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        headers = {"X-Tenant-Id": tenant_id, "X-Caller-Service": self._caller}
        if self._service_token:
            headers["X-Internal-Service"] = self._service_token
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        try:
            response = await self._client.get(PLATFORM_SETTINGS_PATH, headers=headers)
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
        return PlatformSettingsSnapshot(revision=revision, settings=data["settings"])

    def _record_failed(self) -> None:
        inc_counter(PLATFORM_SETTINGS_FETCH_METRIC, 1, {"caller": self._caller, "result": "failed"})

    @staticmethod
    def _decode(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    async def aclose(self) -> None:
        await self._client.aclose()
