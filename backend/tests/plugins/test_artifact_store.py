"""TASK-001（Phase 5）ArtifactStore provider 验收测试。

S-01 / E-02 / B-01 / S-10 / E506 lifecycle·isolation。

真实边界：
- S-01：真实文件系统（tmp）+ 真实 aiosqlite 引擎 + artifact_metadata 表落库；
- E-02：真实 provider 双租户数据（跨租户读取拒绝）；
- B-01：真实 provider 工厂 + SMB 配置；
- S-10：真实 MinIO（docker）端点（不可达时 skip——S-P13-07 不伪造 GREEN）。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fluxion.plugins.artifact import (
    ArtifactStoreError,
    LocalFileArtifactStore,
    build_artifact_ref,
    create_artifact_store,
    parse_artifact_ref,
)
from fluxion.plugins.artifact.s3 import S3CompatibleArtifactStore
from fluxion.registry.schema import artifact_metadata

# ---------------------------------------------------------------------------
# 引擎/表 fixture


@pytest_asyncio.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: artifact_metadata.create(sync_conn, checkfirst=True)
        )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def local_store(tmp_path: Path, engine: AsyncEngine) -> LocalFileArtifactStore:
    return LocalFileArtifactStore(root=tmp_path, engine=engine)


async def _fetch_metadata(engine: AsyncEngine, tenant: str, namespace: str, key: str) -> list[dict]:
    from sqlalchemy import select

    async with engine.connect() as conn:
        rows = await conn.execute(
            select(artifact_metadata).where(
                artifact_metadata.c.tenant_id == tenant,
                artifact_metadata.c.namespace == namespace,
                artifact_metadata.c.key == key,
            )
        )
        return [dict(row._mapping) for row in rows.fetchall()]


# ---------------------------------------------------------------------------
# S-01：local-fs put → get → delete（真实文件系统 + metadata 落表）


class TestS01LocalFileSystemRoundtrip:
    async def test_put_get_delete_roundtrip_and_metadata(
        self, local_store: LocalFileArtifactStore, engine: AsyncEngine
    ) -> None:
        value = b"weekly-report-pdf-bytes"

        await local_store.put("tenant-a", "reports", "2026-w35", value)

        got = await local_store.get("tenant-a", "reports", "2026-w35")
        assert got == value

        rows = await _fetch_metadata(engine, "tenant-a", "reports", "2026-w35")
        assert len(rows) == 1
        row = rows[0]
        assert row["tenant_id"] == "tenant-a"
        assert row["namespace"] == "reports"
        assert row["key"] == "2026-w35"
        assert row["size"] == len(value)
        assert row["sha256"] == hashlib.sha256(value).hexdigest()
        assert row["status"] == "active"
        assert row["version"] == "1"

        await local_store.delete("tenant-a", "reports", "2026-w35")

        with pytest.raises(ArtifactStoreError):
            await local_store.get("tenant-a", "reports", "2026-w35")
        # delete 软删：metadata status=deleted + deleted_at 落值
        rows = await _fetch_metadata(engine, "tenant-a", "reports", "2026-w35")
        assert rows[0]["status"] == "deleted"
        assert rows[0]["deleted_at"] is not None

    async def test_version_increments_per_put(
        self, local_store: LocalFileArtifactStore, engine: AsyncEngine
    ) -> None:
        await local_store.put("tenant-a", "ns", "k", b"v1")
        await local_store.put("tenant-a", "ns", "k", b"v2")

        rows = await _fetch_metadata(engine, "tenant-a", "ns", "k")
        versions = sorted(row["version"] for row in rows)
        assert versions == ["1", "2"]
        # get 返回最新版本内容
        assert await local_store.get("tenant-a", "ns", "k") == b"v2"

    async def test_tenant_namespace_isolation_on_disk(
        self, local_store: LocalFileArtifactStore, tmp_path: Path
    ) -> None:
        await local_store.put("tenant-a", "reports", "shared-key", b"a-data")
        await local_store.put("tenant-b", "reports", "shared-key", b"b-data")

        # 目录前缀按 tenant 隔离（{root}/{tenant}/{ns}/{key}@{version}，版本不可变）
        assert (tmp_path / "tenant-a" / "reports" / "shared-key@1").read_bytes() == b"a-data"
        assert (tmp_path / "tenant-b" / "reports" / "shared-key@1").read_bytes() == b"b-data"
        assert await local_store.get("tenant-a", "reports", "shared-key") == b"a-data"
        assert await local_store.get("tenant-b", "reports", "shared-key") == b"b-data"

    async def test_path_traversal_rejected(self, local_store: LocalFileArtifactStore) -> None:
        with pytest.raises(ArtifactStoreError):
            await local_store.put("tenant-a", "reports", "../../etc/passwd", b"x")

    async def test_timeout_is_bounded(self, local_store: LocalFileArtifactStore) -> None:
        import time

        started = time.monotonic()
        with pytest.raises(ArtifactStoreError):
            # 超时 1ms：任何文件 IO 都应被 deadline 截断（fail policy 有界）
            await local_store.get("tenant-a", "reports", "missing", timeout_ms=1)
        elapsed = time.monotonic() - started
        assert elapsed < 5.0


# ---------------------------------------------------------------------------
# E-02：tenant escape = 0（跨租户读取拒绝）


class TestE02TenantEscapeZero:
    async def test_cross_tenant_read_rejected(
        self, local_store: LocalFileArtifactStore, engine: AsyncEngine
    ) -> None:
        await local_store.put("tenant-a", "reports", "secret-artifact", b"a-private")

        # tenant B 读 tenant A 的 key：provider 按 tenant 前缀寻址 → 不存在 → 明确错误
        with pytest.raises(ArtifactStoreError):
            await local_store.get("tenant-b", "reports", "secret-artifact")

        # metadata 查询同样按 tenant 过滤：B 视角无行
        rows_b = await _fetch_metadata(engine, "tenant-b", "reports", "secret-artifact")
        assert rows_b == []
        rows_a = await _fetch_metadata(engine, "tenant-a", "reports", "secret-artifact")
        assert len(rows_a) == 1

    async def test_cross_tenant_delete_rejected(
        self, local_store: LocalFileArtifactStore
    ) -> None:
        await local_store.put("tenant-a", "reports", "keep-me", b"a-private")
        with pytest.raises(ArtifactStoreError):
            await local_store.delete("tenant-b", "reports", "keep-me")
        # A 的数据未被动
        assert await local_store.get("tenant-a", "reports", "keep-me") == b"a-private"


# ---------------------------------------------------------------------------
# B-01：SMB 注册点（明确错误或降级，不崩溃）


class TestB01SmbRegistrationPoint:
    def test_smb_config_raises_clear_error(self, tmp_path: Path) -> None:
        with pytest.raises(ArtifactStoreError, match="SMB"):
            create_artifact_store(
                "smb", root=tmp_path, engine=None
            )

    def test_unknown_provider_raises_clear_error(self, tmp_path: Path) -> None:
        with pytest.raises(ArtifactStoreError, match="未知"):
            create_artifact_store("mystery", root=tmp_path, engine=None)

    def test_local_factory_returns_local_store(self, tmp_path: Path, engine: AsyncEngine) -> None:
        store = create_artifact_store("local-fs", root=tmp_path, engine=engine)
        assert isinstance(store, LocalFileArtifactStore)


# ---------------------------------------------------------------------------
# artifact:// URI 引用模型


class TestArtifactRef:
    def test_build_and_parse_roundtrip(self) -> None:
        ref = build_artifact_ref(
            tenant_id="tenant-a", namespace="reports", key="2026-w35", version="3"
        )
        assert ref == "artifact://tenant-a/reports/2026-w35@3"
        parsed = parse_artifact_ref(ref)
        assert parsed == ("tenant-a", "reports", "2026-w35", "3")

    def test_parse_rejects_malformed(self) -> None:
        for bad in ["artifact://only-tenant", "http://x/y/z@1", "artifact://a/b@1"]:
            with pytest.raises(ValueError):
                parse_artifact_ref(bad)


# ---------------------------------------------------------------------------
# artifact:// 入 ExecutionSnapshot pin（规则 6/10：published 不可变、执行期固定版本）


from pydantic import ValidationError


class TestArtifactRefSnapshotPin:
    def _snapshot_kwargs(self, artifact_refs: dict[str, str]) -> dict[str, object]:
        from fluxion.resources.contracts import ModelPolicy

        return {
            "execution_id": "exec-1",
            "tenant_id": "tenant-a",
            "user_id": "user-1",
            "runtime_profile_id": "rp-1",
            "runtime_profile_version": "1",
            "model_resolution": ModelPolicy(),
            "trace_id": "trace-1",
            "artifact_refs": artifact_refs,
        }

    def test_snapshot_pins_artifact_refs(self) -> None:
        from fluxion.resources.contracts import ExecutionSnapshot

        snapshot = ExecutionSnapshot.model_validate(
            self._snapshot_kwargs({"weekly-report": "artifact://tenant-a/reports/2026-w35@3"})
        )
        assert snapshot.artifact_refs["weekly-report"] == "artifact://tenant-a/reports/2026-w35@3"
        # 规则 6/10：pin 进 snapshot 后不可变（frozen——与 model_resolution 同语义）
        with pytest.raises(ValidationError):
            snapshot.artifact_refs = {"weekly-report": "artifact://tenant-a/reports/other@1"}  # type: ignore[assignment]

    def test_snapshot_rejects_malformed_artifact_ref(self) -> None:
        from fluxion.resources.contracts import ExecutionSnapshot

        with pytest.raises(ValidationError, match="artifact_ref"):
            ExecutionSnapshot.model_validate(
                self._snapshot_kwargs({"bad": "artifact://tenant-a/reports/missing-version"})
            )


# ---------------------------------------------------------------------------
# S-10：S3CompatibleArtifactStore（真实 MinIO docker；不可达 skip——不伪造 GREEN）


def _minio_endpoint() -> str:
    return os.environ.get("FLUXION_MINIO_ENDPOINT", "http://localhost:9000")


def _minio_available() -> bool:
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(_minio_endpoint())
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 9000), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _minio_available(), reason="MinIO endpoint 不可达（S-10 真实边界）")
class TestS10S3CompatibleArtifactStore:
    @pytest_asyncio.fixture
    async def s3_store(self, engine: AsyncEngine) -> AsyncGenerator[S3CompatibleArtifactStore, None]:
        store = S3CompatibleArtifactStore(
            endpoint=_minio_endpoint(),
            access_key=os.environ.get("FLUXION_MINIO_ACCESS_KEY", "minioadmin"),
            secret_key=os.environ.get("FLUXION_MINIO_SECRET_KEY", "minioadmin"),
            bucket="fluxion-test-artifacts",
            engine=engine,
        )
        await store.initialize()
        try:
            yield store
        finally:
            await store.close()

    async def test_put_get_delete_and_metadata(
        self, s3_store: S3CompatibleArtifactStore, engine: AsyncEngine
    ) -> None:
        value = b"s3-roundtrip-bytes"
        await s3_store.put("tenant-a", "reports", "s3-key", value)

        assert await s3_store.get("tenant-a", "reports", "s3-key") == value

        rows = await _fetch_metadata(engine, "tenant-a", "reports", "s3-key")
        assert len(rows) == 1
        assert rows[0]["sha256"] == hashlib.sha256(value).hexdigest()
        assert rows[0]["size"] == len(value)

        # 跨租户拒绝（tenant escape=0）
        with pytest.raises(ArtifactStoreError):
            await s3_store.get("tenant-b", "reports", "s3-key")

        await s3_store.delete("tenant-a", "reports", "s3-key")
        with pytest.raises(ArtifactStoreError):
            await s3_store.get("tenant-a", "reports", "s3-key")

    async def test_timeout_fail_policy(self, s3_store: S3CompatibleArtifactStore) -> None:
        with pytest.raises(ArtifactStoreError):
            await s3_store.get("tenant-a", "reports", "missing", timeout_ms=1)


# ---------------------------------------------------------------------------
# E506：lifecycle / isolation


class TestE506LifecycleIsolation:
    async def test_provider_registration_failure_leaves_no_partial_registry(
        self, engine: AsyncEngine
    ) -> None:
        """loader 既有回滚：注册中途失败 → registry 无残留（RISK-P5-01）。"""
        from fluxion.plugins.contracts import (
            ArtifactStoreProvider,
            CapabilityDescriptor,
            PluginManifest,
            PluginType,
            TrustLevel,
        )
        from fluxion.plugins.loader import PluginLoader, ProviderNotFoundError

        class _ArtifactPlugin(ArtifactStoreProvider):
            """manifest 属性 + setup/shutdown + typed SPI；broken 标志控制 capabilities 抛错。"""

            def __init__(self, manifest: PluginManifest, broken: bool) -> None:
                self._manifest = manifest
                self._broken = broken

            @property
            def manifest(self) -> PluginManifest:
                return self._manifest

            async def setup(self, ctx: object) -> None:
                return None

            async def shutdown(self) -> None:
                return None

            def capabilities(self) -> list[CapabilityDescriptor]:
                if self._broken:
                    raise RuntimeError("capability introspection failed")
                return []

            async def put(self, *args: object, **kwargs: object) -> None: ...
            async def get(self, *args: object, **kwargs: object) -> bytes: ...
            async def delete(self, *args: object, **kwargs: object) -> None: ...

        def manifest_for(plugin_id: str) -> PluginManifest:
            return PluginManifest(
                plugin_id=plugin_id,
                version="1.0.0",
                plugin_type=PluginType.ARTIFACT_STORE,
                entrypoint="builtin",
                trust_level=TrustLevel.TRUSTED,
                permissions=[],
                dependencies=[],
                compatibility={},
            )

        loader = PluginLoader()

        # 正常注册：registry 可 resolve
        ok = _ArtifactPlugin(manifest_for("ok-artifact"), broken=False)
        await loader.load(ok)  # type: ignore[arg-type]
        assert loader.registry_for(PluginType.ARTIFACT_STORE).resolve("ok-artifact") is ok

        # 注册中途失败（capabilities 抛错）→ loader 回滚，无 partial registry
        broken = _ArtifactPlugin(manifest_for("broken-artifact"), broken=True)
        with pytest.raises(RuntimeError, match="capability introspection failed"):
            await loader.load(broken)  # type: ignore[arg-type]
        assert loader.loaded == [record for record in loader.loaded if record.manifest.plugin_id != "broken-artifact"]
        with pytest.raises(ProviderNotFoundError):
            loader.registry_for(PluginType.ARTIFACT_STORE).resolve("broken-artifact")

    async def test_single_provider_fault_does_not_crash_runtime(
        self, local_store: LocalFileArtifactStore
    ) -> None:
        """单 provider 故障（get 缺 key）→ ArtifactStoreError 上抛，不拖垮进程。"""
        try:
            await local_store.get("tenant-a", "ns", "no-such-key")
            raise AssertionError("expected ArtifactStoreError")
        except ArtifactStoreError:
            pass
        # provider 仍可用（故障不持久）
        await local_store.put("tenant-a", "ns", "after-fault", b"still-works")
        assert await local_store.get("tenant-a", "ns", "after-fault") == b"still-works"
