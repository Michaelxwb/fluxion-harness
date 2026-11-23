"""API-28：Task-owned 产物的写入 / 追加不可变版本（Worker 侧客户端）。

契约只描述版本元信息；字节随同一次 multipart 请求进入 Owner（存储键由 Runtime 按
tenant/Execution 生成，调用方不声明归属）。声明的 size/checksum 与字节不符时由 Owner 显式拒绝，
本客户端据实计算，不猜测。
"""

from __future__ import annotations

import json
from uuid import UUID

import httpx
from muad_contracts import (
    ArtifactPurpose,
    AttachmentRef,
    TaskArtifactWriteRequest,
    TaskArtifactWriteResponse,
)

ARTIFACTS_PATH = "/internal/artifacts"
DEFAULT_TIMEOUT_SEC = 10.0


class TaskArtifactError(Exception):
    """写入没有得出结论：`code` 是 Runtime 的稳定错误码（无结论时 `TASK_ARTIFACT_UNAVAILABLE`）。"""

    def __init__(self, message: str, *, code: str = "TASK_ARTIFACT_UNAVAILABLE") -> None:
        super().__init__(message)
        self.code = code


class TaskArtifactClient:
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

    async def read(
        self,
        *,
        tenant_id: str,
        execution_id: UUID,
        artifact_id: str,
    ) -> AttachmentRef:
        headers = {"X-Tenant-Id": tenant_id}
        if self._service_token:
            headers["X-Internal-Service"] = self._service_token
        try:
            response = await self._client.get(
                f"{self._base_url}{ARTIFACTS_PATH}/{artifact_id}",
                params={"task_execution_id": str(execution_id)},
                headers=headers,
                timeout=self._timeout_sec,
            )
        except httpx.HTTPError as exc:
            raise TaskArtifactError(type(exc).__name__) from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise TaskArtifactError(f"http_{response.status_code}") from exc
        if not isinstance(body, dict):
            raise TaskArtifactError(f"http_{response.status_code}")
        if body.get("code") != "0":
            raise TaskArtifactError(str(body.get("code")), code=str(body.get("code")))
        data = body.get("data")
        if not isinstance(data, dict):
            raise TaskArtifactError("malformed_artifact_response")
        try:
            return AttachmentRef.model_validate(data)
        except ValueError as exc:
            raise TaskArtifactError("malformed_artifact_response") from exc

    async def write(
        self,
        *,
        tenant_id: str,
        execution_id: UUID,
        filename: str,
        media_type: str,
        content: bytes,
        checksum: str,
        base_version: int = 0,
        purpose: ArtifactPurpose = "OUTPUT",
    ) -> TaskArtifactWriteResponse:
        request = TaskArtifactWriteRequest(
            task_execution_id=execution_id,
            purpose=purpose,
            filename=filename,
            media_type=media_type,
            size=len(content),
            checksum=checksum,
            base_version=base_version,
        )
        headers = {"X-Tenant-Id": tenant_id}
        if self._service_token:
            headers["X-Internal-Service"] = self._service_token
        try:
            response = await self._client.post(
                f"{self._base_url}{ARTIFACTS_PATH}",
                data={"metadata": json.dumps(request.model_dump(mode="json"), ensure_ascii=False)},
                files={"content": (filename, content, media_type)},
                headers=headers,
                timeout=self._timeout_sec,
            )
        except httpx.HTTPError as exc:
            raise TaskArtifactError(type(exc).__name__) from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise TaskArtifactError(f"http_{response.status_code}") from exc
        if not isinstance(body, dict):
            raise TaskArtifactError(f"http_{response.status_code}")
        code = body.get("code")
        if code != "0":
            raise TaskArtifactError(str(code), code=str(code))
        data = body.get("data")
        if not isinstance(data, dict):
            raise TaskArtifactError("malformed_artifact_response")
        try:
            return TaskArtifactWriteResponse.model_validate(data)
        except ValueError as exc:
            raise TaskArtifactError("malformed_artifact_response") from exc


__all__ = ["TaskArtifactClient", "TaskArtifactError"]
