"""Registry Snapshot 一致读契约（TASK-024 / ADR-A016）验收测试。

覆盖 B-SNAP-DESIGN-01：
- Store scoped-read Protocol 与事务契约声明；
- 只读 scope/tenant/timeout/错误类型齐全；
- 本任务仅契约校验，PG 行为由 025/026 验收。
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from fluxion.registry.store import (
    RegistryReadStore,
    RegistryStoreError,
    ResourceBinding,
    ResourceDefinition,
    ResourceKind,
    ScopedReadConflictError,
    ScopedReadStore,
    ScopedReadTimeoutError,
    ScopedRegistryReader,
)


class _FakeScopedReader:
    """最小假实现：证明实现方契约可满足（非 PG 行为验收）。"""

    def __init__(self, tenant_id: str, revision: int) -> None:
        self._tenant_id = tenant_id
        self._revision = revision

    async def get(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        version: str | None = None,
    ) -> ResourceDefinition | None:
        assert tenant_id == self._tenant_id
        return None

    async def read_revision(self) -> int:
        return self._revision

    async def list_bindings(
        self,
        *,
        subject_type: str,
        subject_id: str,
        tenant_id: str,
        resource_type: ResourceKind | None = None,
    ) -> list[ResourceBinding]:
        assert tenant_id == self._tenant_id
        return []

    async def get_user_profile_at(
        self, *, tenant_id: str, platform_user_id: str, version: str
    ) -> dict[str, object] | None:
        assert tenant_id == self._tenant_id
        return None

    async def get_latest_user_profile(
        self, *, tenant_id: str, platform_user_id: str
    ) -> dict[str, object] | None:
        assert tenant_id == self._tenant_id
        return None

    async def list_capability_grants(
        self, *, tenant_id: str, platform_user_id: str
    ) -> list[object]:
        assert tenant_id == self._tenant_id
        return []

    async def list_resources(
        self,
        kind: ResourceKind,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
        keyword: str | None = None,
        resource_id: str | None = None,
        status: object | None = None,
    ) -> tuple[list[ResourceDefinition], int]:
        assert tenant_id == self._tenant_id
        return [], 0


class _FakeScopedStore:
    @asynccontextmanager
    async def begin_scoped_read(
        self, *, tenant_id: str, timeout_ms: int = 5_000
    ) -> AsyncIterator[_FakeScopedReader]:
        yield _FakeScopedReader(tenant_id, 7)

    async def get(
        self,
        kind: ResourceKind,
        resource_id: str,
        *,
        tenant_id: str,
        version: str | None = None,
    ) -> ResourceDefinition | None:
        return None

    async def read_revision(self, *, tenant_id: str) -> int:
        return 7

    async def list_bindings(
        self,
        *,
        subject_type: str,
        subject_id: str,
        tenant_id: str,
        resource_type: ResourceKind | None = None,
    ) -> list[ResourceBinding]:
        return []


def test_B_SNAP_DESIGN_01_scoped_protocols_declared() -> None:
    """B-SNAP-DESIGN-01：scoped-read Protocol 声明齐全，假实现可满足。"""
    assert isinstance(_FakeScopedReader("t", 1), ScopedRegistryReader)
    assert isinstance(_FakeScopedStore(), ScopedReadStore)
    assert isinstance(_FakeScopedStore(), RegistryReadStore)


def test_B_SNAP_DESIGN_01_scope_tenant_timeout_required() -> None:
    """B-SNAP-DESIGN-01：只读 scope/tenant/timeout 为强制签名。"""
    params = inspect.signature(ScopedReadStore.begin_scoped_read).parameters
    assert params["tenant_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["timeout_ms"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["timeout_ms"].default == 5_000


def test_B_SNAP_DESIGN_01_error_types_typed() -> None:
    """B-SNAP-DESIGN-01：超时/冲突为类型化错误，带 code，可区分捕获。"""
    assert issubclass(ScopedReadTimeoutError, RegistryStoreError)
    assert issubclass(ScopedReadConflictError, RegistryStoreError)
    assert ScopedReadTimeoutError.code == "scoped_read_timeout"
    assert ScopedReadConflictError.code == "scoped_read_conflict"
    with pytest.raises(RegistryStoreError):
        raise ScopedReadTimeoutError("timeout")


async def _use_reader() -> int:
    store = _FakeScopedStore()
    async with store.begin_scoped_read(tenant_id="t", timeout_ms=100) as reader:
        return await reader.read_revision()


def test_B_SNAP_DESIGN_01_scoped_read_usable() -> None:
    """B-SNAP-DESIGN-01：scoped read 可用（async 上下文 + revision 可见）。"""
    assert asyncio.run(_use_reader()) == 7
