"""LocalFileArtifactStore：ArtifactStoreProvider 的 dev 实现（Phase 5 TASK-001）。

- 目录前缀 `{root}/{tenant_id}/{namespace}/{key}@{version}`（版本不可变，get 取最新）；
- `artifact_metadata` 表落治理事实（audit/retention/GC，remediation §16.2）；
- 全方法 timeout + fail policy（规则 18）：文件 IO 经 `asyncio.to_thread`，
  整体经 `asyncio.wait_for`（deadline 截断 → ArtifactStoreError）；
- tenant 前缀即隔离边界（E-02 tenant escape=0）+ path traversal 拒绝。
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.registry.schema import artifact_metadata


class ArtifactStoreError(RuntimeError):
    """ArtifactStore put/get/delete 失败（含超时/不存在/路径非法）。"""

    code = "artifact_store_error"


def _validate_segments(*segments: str) -> None:
    """路径段防穿越：禁止空段与 `..`（tenant/namespace/key 进入文件系统前收口）。"""
    for segment in segments:
        if not segment or not segment.strip() or segment in {".", ".."} or "/" in segment or "\\" in segment:
            raise ArtifactStoreError(f"非法 artifact 路径段: {segment!r}")


class LocalFileArtifactStore:
    """本地文件系统 dev provider（必须通；生产用 S3CompatibleArtifactStore）。"""

    def __init__(self, root: Path, engine: AsyncEngine) -> None:
        self._root = Path(root)
        self._engine = engine

    async def initialize(self) -> None:
        """幂等建表（artifact_metadata）+ 根目录。"""
        self._root.mkdir(parents=True, exist_ok=True)
        async with self._engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: artifact_metadata.create(sync_conn, checkfirst=True)
            )

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
        except (OSError, SQLAlchemyError) as error:
            raise ArtifactStoreError(f"put 失败: {key}: {error}") from error

    async def get(
        self, tenant_id: str, namespace: str, key: str, timeout_ms: int = 30_000
    ) -> bytes:
        _validate_segments(tenant_id, namespace, key)
        try:
            return await asyncio.wait_for(
                self._get(tenant_id, namespace, key), timeout=timeout_ms / 1000
            )
        except TimeoutError as error:
            raise ArtifactStoreError(f"get 超时（{timeout_ms}ms）: {key}") from error
        except FileNotFoundError as error:
            raise ArtifactStoreError(f"artifact 不存在: {tenant_id}/{namespace}/{key}") from error
        except (OSError, SQLAlchemyError) as error:
            raise ArtifactStoreError(f"get 失败: {key}: {error}") from error

    async def delete(
        self, tenant_id: str, namespace: str, key: str, timeout_ms: int = 30_000
    ) -> None:
        _validate_segments(tenant_id, namespace, key)
        try:
            await asyncio.wait_for(
                self._delete(tenant_id, namespace, key), timeout=timeout_ms / 1000
            )
        except TimeoutError as error:
            raise ArtifactStoreError(f"delete 超时（{timeout_ms}ms）: {key}") from error
        except (OSError, SQLAlchemyError) as error:
            raise ArtifactStoreError(f"delete 失败: {key}: {error}") from error

    # ---- 内部实现（deadline 内执行）----

    async def _put(self, tenant_id: str, namespace: str, key: str, value: bytes) -> None:
        version = await self._next_version(tenant_id, namespace, key)
        blob_path = self._blob_path(tenant_id, namespace, key, version)
        await asyncio.to_thread(self._write_blob, blob_path, value)
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

    async def _get(self, tenant_id: str, namespace: str, key: str) -> bytes:
        version = await self._latest_version(tenant_id, namespace, key)
        if version is None:
            raise FileNotFoundError(f"artifact 不存在: {tenant_id}/{namespace}/{key}")
        blob_path = self._blob_path(tenant_id, namespace, key, version)
        return await asyncio.to_thread(blob_path.read_bytes)

    async def _delete(self, tenant_id: str, namespace: str, key: str) -> None:
        version = await self._latest_version(tenant_id, namespace, key)
        if version is None:
            # 不存在（含跨租户：tenant 前缀下无此 key）→ 明确拒绝（E-02）
            raise FileNotFoundError(f"artifact 不存在: {tenant_id}/{namespace}/{key}")
        blob_path = self._blob_path(tenant_id, namespace, key, version)
        await asyncio.to_thread(blob_path.unlink, True)
        # 软删（治理事实）：status=deleted + deleted_at；blob 清理归 Retention/GC
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
        if current is None:
            return "1"
        return str(int(current) + 1)

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

    def _blob_path(self, tenant_id: str, namespace: str, key: str, version: str) -> Path:
        return self._root / tenant_id / namespace / f"{key}@{version}"

    @staticmethod
    def _write_blob(blob_path: Path, value: bytes) -> None:
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_bytes(value)

    async def purge_deleted(self, tenant_id: str, namespace: str, key: str) -> int:
        """物理清除已软删的 metadata 行（Retention/GC 辅助；返回删除行数）。"""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa_delete(artifact_metadata).where(
                    artifact_metadata.c.tenant_id == tenant_id,
                    artifact_metadata.c.namespace == namespace,
                    artifact_metadata.c.key == key,
                    artifact_metadata.c.status == "deleted",
                )
            )
            return result.rowcount or 0
