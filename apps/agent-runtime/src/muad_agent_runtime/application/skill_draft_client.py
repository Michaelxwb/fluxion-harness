"""Runtime → Console 草稿内部 API 客户端（API-03 / API-10 取件与回写）。

- 取件：``GET /internal/skills/drafts/{draft_id}/archive``，二进制 zip + ``X-Draft-Revision``；
- 回写：``POST /internal/skills/drafts/{draft_id}/apply``，一次批量 + revision CAS；
- 只携带受信 Run 上下文里的 tenant/owner，模型参数不能覆盖；
- 不记录草稿内容/Cookie/Token；错误码原样透传（DRAFT_REVISION_CONFLICT 等）。
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from muad_agent_core.skill.draft_shell import (
    DraftShellApplyResult,
    DraftShellArchive,
    DraftShellChange,
    DraftShellError,
    DraftShellRequest,
)

INTERNAL_SERVICE_HEADER = "X-Internal-Service"
REVISION_HEADER = "X-Draft-Revision"
DEFAULT_TIMEOUT_SEC = 30.0
logger = logging.getLogger(__name__)


class ConsoleDraftShellClient:
    """草稿 Shell 的 Console 适配器；可注入 ASGI 客户端用于验收。"""

    def __init__(
        self,
        base_url: str,
        *,
        service_token: str | None = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if timeout_sec <= 0:
            raise ValueError("draft shell client timeout_sec must be positive")
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._timeout_sec = timeout_sec
        self._client = client
        self._owns_client = client is None

    async def fetch_archive(self, request: DraftShellRequest) -> DraftShellArchive:
        path = f"/internal/skills/drafts/{request.draft_id}/archive"
        response = await self._request(
            "GET",
            path,
            params={"tenant_id": request.tenant_id, "owner_user_id": request.owner_user_id},
            accept="application/zip",
        )
        if response.status_code >= 400:
            raise _response_error(response)
        revision = response.headers.get(REVISION_HEADER, "")
        if not revision.isdigit():
            logger.warning("draft_shell_archive_missing_revision draft_id=%s", request.draft_id)
            raise DraftShellError("COMMON_INTERNAL_ERROR", "draft archive revision is missing")
        return DraftShellArchive(
            draft_id=request.draft_id,
            revision=int(revision),
            zip_bytes=response.content,
        )

    async def apply_changes(
        self,
        request: DraftShellRequest,
        *,
        expected_revision: int,
        changes: Sequence[DraftShellChange],
        deletions: Sequence[str],
    ) -> DraftShellApplyResult:
        body: dict[str, Any] = {
            "tenant_id": request.tenant_id,
            "owner_user_id": request.owner_user_id,
            "expected_revision": expected_revision,
            "changes": [_encoded_change(change) for change in changes],
            "deletions": list(deletions),
        }
        response = await self._request(
            "POST",
            f"/internal/skills/drafts/{request.draft_id}/apply",
            json=body,
        )
        if response.status_code >= 400:
            raise _response_error(response)
        payload = _payload(response)
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("revision"), int):
            logger.warning("draft_shell_apply_envelope_invalid draft_id=%s", request.draft_id)
            raise DraftShellError("COMMON_INTERNAL_ERROR", "draft apply response is invalid")
        changed = data.get("changed_files")
        return DraftShellApplyResult(
            revision=int(data["revision"]),
            changed_files=tuple(str(item) for item in changed) if isinstance(changed, list) else (),
        )

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        accept: str = "application/json",
    ) -> httpx.Response:
        headers = {"Accept": accept}
        if self._service_token:
            headers[INTERNAL_SERVICE_HEADER] = self._service_token
        url = f"{self._base_url}{path}"
        try:
            if self._client is not None:
                return await self._client.request(
                    method, url, params=params, json=json, headers=headers
                )
            async with httpx.AsyncClient(timeout=self._timeout_sec, trust_env=False) as client:
                return await client.request(method, url, params=params, json=json, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning(
                "draft_shell_console_unavailable method=%s path=%s error=%s",
                method,
                path,
                type(exc).__name__,
            )
            raise DraftShellError(
                "COMMON_INTERNAL_ERROR", "console draft API is unavailable"
            ) from exc


def _encoded_change(change: DraftShellChange) -> dict[str, str]:
    try:
        return {"path": change.path, "content": change.content.decode("utf-8")}
    except UnicodeDecodeError:
        return {
            "path": change.path,
            "content_base64": base64.b64encode(change.content).decode("ascii"),
        }


def _payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise DraftShellError("COMMON_INTERNAL_ERROR", "console returned an invalid envelope") from exc
    if not isinstance(payload, dict):
        raise DraftShellError("COMMON_INTERNAL_ERROR", "console returned an invalid envelope")
    return payload


def _response_error(response: httpx.Response) -> DraftShellError:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    code = payload.get("code") if isinstance(payload, dict) else None
    message = payload.get("msg") if isinstance(payload, dict) else None
    return DraftShellError(
        code if isinstance(code, str) and code else "COMMON_INTERNAL_ERROR",
        message if isinstance(message, str) and message else f"console returned HTTP {response.status_code}",
    )


__all__ = ["ConsoleDraftShellClient"]
