"""S3CompatibleArtifactStore：ArtifactStoreProvider 生产实现（Phase 5 TASK-001）。

S3/MinIO 兼容 endpoint（remediation §16.1 翻案：生产必须落地 S3 兼容，SMB 仅预留）。

- 对象键 `{tenant}/{namespace}/{key}@{version}`（版本不可变，get 取最新）；
- blob 落对象存储，治理事实落 `artifact_metadata` 表（remediation §16.2）；
- timeout/retry/fail policy（规则 18，新增外部依赖）：httpx deadline =
  `timeout_ms`，连接类错误有界重试（≤2 次），最终失败 → ArtifactStoreError；
- SigV4 签名（stdlib hmac/hashlib，path-style 寻址——MinIO 默认）。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.registry.schema import artifact_metadata

from .local_fs import ArtifactStoreError, _validate_segments

_MAX_RETRIES = 2


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def _sigv4_headers(
    *,
    method: str,
    endpoint: str,
    canonical_uri: str,
    payload: bytes,
    access_key: str,
    secret_key: str,
    region: str,
) -> dict[str, str]:
    """AWS SigV4 签名头（path-style：Host + x-amz-content-sha256 + x-amz-date）。"""
    parsed = urlparse(endpoint)
    host = parsed.netloc
    amz_date = dt.datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    date_stamp = amz_date[:8]
    payload_hash = hashlib.sha256(payload).hexdigest()
    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in sorted(headers))
    # SigV4 canonical request 是固定顺序多行拼接，join(list) 最可读（f-string 不适用）
    canonical_request = "\n".join(  # noqa: FLY002
        [method, canonical_uri, "", canonical_headers, signed_headers, payload_hash]
    )
    scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    signing_key = _sign(
        _sign(_sign(_sign(f"AWS4{secret_key}".encode(), date_stamp), region), "s3"),
        "aws4_request",
    )
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return {
        "Authorization": authorization,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }


class S3CompatibleArtifactStore:
    """S3/MinIO 兼容生产 provider。"""

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        engine: AsyncEngine,
        region: str = "us-east-1",
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket = bucket
        self._engine = engine
        self._region = region
        self._client: httpx.AsyncClient | None = None

    async def initialize(self) -> None:
        """幂等建 bucket（HEAD 探测 → 404 则 PUT）+ artifact_metadata 表。"""
        self._client = httpx.AsyncClient(timeout=10.0)
        async with self._engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: artifact_metadata.create(sync_conn, checkfirst=True)
            )
        status = await self._request("HEAD", f"/{self._bucket}", b"", timeout_ms=5_000)
        if status == 404:
            await self._request("PUT", f"/{self._bucket}", b"", timeout_ms=5_000)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def put(
        self, tenant_id: str, namespace: str, key: str, value: bytes, timeout_ms: int = 30_000
    ) -> None:
        _validate_segments(tenant_id, namespace, key)
        try:
            await asyncio.wait_for(
                self._put(tenant_id, namespace, key, value), timeout=timeout_ms / 1000
            )
        except TimeoutError as error:
            raise ArtifactStoreError(f"put 超时（{timeout_ms}ms）: {key}") from error
        except (httpx.HTTPError, SQLAlchemyError) as error:
            raise ArtifactStoreError(f"put 失败: {key}: {error}") from error

    async def get(
        self, tenant_id: str, namespace: str, key: str, timeout_ms: int = 30_000
    ) -> bytes:
        _validate_segments(tenant_id, namespace, key)
        try:
            return await asyncio.wait_for(
                self._get(tenant_id, namespace, key, timeout_ms), timeout=timeout_ms / 1000
            )
        except TimeoutError as error:
            raise ArtifactStoreError(f"get 超时（{timeout_ms}ms）: {key}") from error
        except _ObjectNotFound as error:
            raise ArtifactStoreError(f"artifact 不存在: {tenant_id}/{namespace}/{key}") from error
        except (httpx.HTTPError, SQLAlchemyError) as error:
            raise ArtifactStoreError(f"get 失败: {key}: {error}") from error

    async def delete(
        self, tenant_id: str, namespace: str, key: str, timeout_ms: int = 30_000
    ) -> None:
        _validate_segments(tenant_id, namespace, key)
        try:
            await asyncio.wait_for(
                self._delete(tenant_id, namespace, key, timeout_ms), timeout=timeout_ms / 1000
            )
        except TimeoutError as error:
            raise ArtifactStoreError(f"delete 超时（{timeout_ms}ms）: {key}") from error
        except (httpx.HTTPError, SQLAlchemyError) as error:
            raise ArtifactStoreError(f"delete 失败: {key}: {error}") from error

    # ---- 内部实现 ----

    async def _put(self, tenant_id: str, namespace: str, key: str, value: bytes) -> None:
        version = await self._next_version(tenant_id, namespace, key)
        object_uri = self._object_uri(tenant_id, namespace, key, version)
        status = await self._request("PUT", object_uri, value, timeout_ms=30_000)
        if status not in (200, 201):
            raise ArtifactStoreError(f"S3 put 返回 {status}: {key}")
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(artifact_metadata).values(
                    artifact_id=uuid.uuid4().hex,
                    tenant_id=tenant_id,
                    namespace=namespace,
                    key=key,
                    version=version,
                    size=len(value),
                    sha256=hashlib.sha256(value).hexdigest(),
                    status="active",
                )
            )

    async def _get(self, tenant_id: str, namespace: str, key: str, timeout_ms: int) -> bytes:
        version = await self._latest_version(tenant_id, namespace, key)
        if version is None:
            raise _ObjectNotFound(f"artifact 不存在: {tenant_id}/{namespace}/{key}")
        object_uri = self._object_uri(tenant_id, namespace, key, version)
        status, body = await self._request_body("GET", object_uri, timeout_ms)
        if status == 404:
            raise _ObjectNotFound(f"对象缺失: {object_uri}")
        if status != 200:
            raise ArtifactStoreError(f"S3 get 返回 {status}: {key}")
        return body

    async def _delete(self, tenant_id: str, namespace: str, key: str, timeout_ms: int) -> None:
        version = await self._latest_version(tenant_id, namespace, key)
        if version is not None:
            object_uri = self._object_uri(tenant_id, namespace, key, version)
            status = await self._request("DELETE", object_uri, b"", timeout_ms)
            if status not in (200, 204):
                raise ArtifactStoreError(f"S3 delete 返回 {status}: {key}")
        async with self._engine.begin() as conn:
            await conn.execute(
                update(artifact_metadata)
                .where(
                    artifact_metadata.c.tenant_id == tenant_id,
                    artifact_metadata.c.namespace == namespace,
                    artifact_metadata.c.key == key,
                    artifact_metadata.c.status == "active",
                )
                .values(status="deleted", deleted_at=datetime.now(UTC))
            )

    async def _next_version(self, tenant_id: str, namespace: str, key: str) -> str:
        current = await self._latest_version(tenant_id, namespace, key)
        return "1" if current is None else str(int(current) + 1)

    async def _latest_version(self, tenant_id: str, namespace: str, key: str) -> str | None:
        async with self._engine.connect() as conn:
            row: Any = await conn.execute(
                select(func.max(artifact_metadata.c.version)).where(
                    artifact_metadata.c.tenant_id == tenant_id,
                    artifact_metadata.c.namespace == namespace,
                    artifact_metadata.c.key == key,
                    artifact_metadata.c.status == "active",
                )
            )
            value: str | None = row.scalar_one_or_none()
            return value

    def _object_uri(self, tenant_id: str, namespace: str, key: str, version: str) -> str:
        return f"/{self._bucket}/{tenant_id}/{namespace}/{key}@{version}"

    async def _request(
        self, method: str, canonical_uri: str, payload: bytes, timeout_ms: int
    ) -> int:
        """带界重试的 S3 请求（连接类错误重试 ≤_MAX_RETRIES；返回状态码）。"""
        attempts = _MAX_RETRIES + 1
        last_error: Exception | None = None
        for _ in range(attempts):
            assert self._client is not None
            headers = self._signed_headers(method, canonical_uri, payload)
            try:
                response = await self._client.request(
                    method,
                    f"{self._endpoint}{_encode_uri(canonical_uri)}",
                    content=payload,
                    headers=headers,
                    timeout=timeout_ms / 1000,
                )
                return response.status_code
            except httpx.TransportError as error:
                last_error = error
                continue
        raise ArtifactStoreError(f"S3 请求失败（重试 {_MAX_RETRIES} 次）: {last_error}")

    async def _request_body(
        self, method: str, canonical_uri: str, timeout_ms: int
    ) -> tuple[int, bytes]:
        assert self._client is not None
        headers = self._signed_headers(method, canonical_uri, b"")
        response = await self._client.request(
            method,
            f"{self._endpoint}{_encode_uri(canonical_uri)}",
            headers=headers,
            timeout=timeout_ms / 1000,
        )
        return response.status_code, response.content

    def _signed_headers(
        self, method: str, canonical_uri: str, payload: bytes
    ) -> dict[str, str]:
        """SigV4 签名头：canonical URI 经 RFC 3986 编码（`@`→%40，MinIO 实测要求）。"""
        return _sigv4_headers(
            method=method,
            endpoint=self._endpoint,
            canonical_uri=_encode_uri(canonical_uri),
            payload=payload,
            access_key=self._access_key,
            secret_key=self._secret_key,
            region=self._region,
        )


class _ObjectNotFound(Exception):
    """内部信号：对象/版本不存在（对外统一 ArtifactStoreError）。"""


def _encode_uri(path: str) -> str:
    """RFC 3986 路径编码（保留 `/`；`@` 等保留字编码——MinIO SigV4 实测要求）。"""
    return quote(path, safe="/")
