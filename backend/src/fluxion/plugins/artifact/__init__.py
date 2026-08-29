"""ArtifactStore provider 包（Phase 5 TASK-001）。

生产 `S3CompatibleArtifactStore`（S3/MinIO 兼容）+ dev `LocalFileArtifactStore`；
SMB 注册点仅预留（B-01：配置 SMB → 明确「SMB 未实现」错误，不崩溃）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from .local_fs import ArtifactStoreError, LocalFileArtifactStore
from .refs import ArtifactRef, build_artifact_ref, parse_artifact_ref, parse_ref
from .s3 import S3CompatibleArtifactStore

__all__ = [
    "ArtifactRef",
    "ArtifactStoreError",
    "LocalFileArtifactStore",
    "S3CompatibleArtifactStore",
    "build_artifact_ref",
    "create_artifact_store",
    "parse_artifact_ref",
    "parse_ref",
]


def create_artifact_store(
    provider: str,
    *,
    root: Path,
    engine: AsyncEngine | None,
    endpoint: str | None = None,
    access_key: str | None = None,
    secret_key: str | None = None,
    bucket: str | None = None,
    **_extra: Any,
) -> LocalFileArtifactStore | S3CompatibleArtifactStore:
    """按 provider 名构造 store（配置入口；B-01：SMB/未知 → 明确错误不崩溃）。"""
    if provider == "local-fs":
        if engine is None:
            raise ArtifactStoreError("local-fs provider 需要 engine（artifact_metadata 落库）")
        return LocalFileArtifactStore(root=root, engine=engine)
    if provider == "s3":
        if engine is None or endpoint is None or access_key is None or secret_key is None or bucket is None:
            raise ArtifactStoreError(
                "s3 provider 需要 engine/endpoint/access_key/secret_key/bucket"
            )
        return S3CompatibleArtifactStore(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
            engine=engine,
        )
    if provider == "smb":
        # SMB 注册点预留（remediation §16.1：生产走 S3 兼容；SMB 未实现 → 明确报错）
        raise ArtifactStoreError(
            "SMB provider 未实现（生产请使用 s3 兼容 provider；SMB 仅为接口预留）"
        )
    raise ArtifactStoreError(f"未知 artifact provider: {provider}")
