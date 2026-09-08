"""一致配置读取与快照组装（TASK-025 / ADR-A016）验收测试。

覆盖 S-SNAP-01 / E-SNAP-01：
- 真实边界：真实 ContextResolver → Store scoped read → PostgreSQL；
  真实事务读 → 超时/配置冲突 → Resolver/cache。
"""

from __future__ import annotations

import pytest

from fluxion.registry import PostgreSQLRegistryStore
from fluxion.registry.store import ScopedReadTimeoutError
from fluxion.resources import ResourceKind
from fluxion.services.runtime_app import (
    CreateRuntimeProfileRequest,
    PublishRuntimeProfileRequest,
)
from tests.runtime_helpers import TEST_POSTGRES_DSN


async def _seed_profile(
    store: PostgreSQLRegistryStore, version: str, *, default: bool = False
) -> None:
    from fluxion.services.runtime_profile_service import RuntimeProfileService

    service = RuntimeProfileService(store)
    await service.create_runtime_profile(
        CreateRuntimeProfileRequest(
            tenant_id="tenant-a",
            runtime_profile_id="assistant",
            version=version,
            default=default,
        )
    )
    await service.publish_runtime_profile(
        PublishRuntimeProfileRequest(
            tenant_id="tenant-a", runtime_profile_id="assistant", version=version
        )
    )


@pytest.mark.asyncio
async def test_S_SNAP_01_scoped_read_sees_consistent_view() -> None:
    """S-SNAP-01：同一 scope 内配置来自一致视图；只读 + tenant scope 有效。"""
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    try:
        await _seed_profile(store, "1")
        async with store.begin_scoped_read(tenant_id="tenant-a") as reader:
            first = await reader.get(
                ResourceKind.RUNTIME_PROFILE, "assistant", tenant_id="tenant-a"
            )
            assert first is not None
            assert first.version == "1"
            revision = await reader.read_revision()
            # scope 外并发发布 v2（独立连接、已提交）。
            await _seed_profile(store, "2")
            second = await reader.get(
                ResourceKind.RUNTIME_PROFILE, "assistant", tenant_id="tenant-a"
            )
            assert second is not None
            # REPEATABLE READ：scope 内仍见 v1 + 同一 revision，无混合发布窗口。
            assert second.version == "1"
            assert await reader.read_revision() == revision
            bindings = await reader.list_bindings(
                subject_type="user",
                subject_id="user-a",
                tenant_id="tenant-a",
            )
            assert bindings == []
        # scope 外可见新提交。
        latest = await store.get(
            ResourceKind.RUNTIME_PROFILE, "assistant", tenant_id="tenant-a"
        )
        assert latest is not None
        assert latest.version == "2"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_S_SNAP_01_scoped_read_enforces_tenant() -> None:
    """S-SNAP-01：scoped reader 强制 tenant，不可串户。"""
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    try:
        await _seed_profile(store, "1")
        async with store.begin_scoped_read(tenant_id="tenant-a") as reader:
            with pytest.raises(ValueError, match="tenant"):
                await reader.get(
                    ResourceKind.RUNTIME_PROFILE, "assistant", tenant_id="tenant-b"
                )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_E_SNAP_01_scoped_read_timeout_is_typed() -> None:
    """E-SNAP-01：有界失败——连接拿不到时类型化超时，无无穷等待。"""
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    try:
        with pytest.raises(ScopedReadTimeoutError):
            async with store.begin_scoped_read(tenant_id="tenant-a", timeout_ms=0):
                pass  # pragma: no cover - 超时即失败，进不到这里
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_E_SNAP_01_failed_resolve_does_not_poison_cache() -> None:
    """E-SNAP-01：异常不污染缓存——失败 resolve 后有效 resolve 仍工作。"""
    from fluxion.services.context_resolver import (
        ContextResolutionError,
        ContextResolver,
        ResolverSelector,
    )
    from tests.runtime_helpers import seed_agent_definition

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    try:
        await _seed_profile(store, "1", default=True)
        await seed_agent_definition(store, provider_id="dev.echo", model_name="dev")
        resolver = ContextResolver(store)
        request_id = f"req_{'a' * 32}"
        trace_id = f"trace_{'a' * 32}"
        execution_id = f"exec_{'a' * 32}"
        with pytest.raises(ContextResolutionError):
            await resolver.resolve(
                ResolverSelector(
                    tenant_id="tenant-a", agent_id="ghost", user_id="user-a"
                ),
                session_id="s-fail",
                request_id=request_id,
                trace_id=trace_id,
                execution_id=execution_id,
            )
        result = await resolver.resolve(
            ResolverSelector(
                tenant_id="tenant-a", agent_id="assistant", user_id="user-a"
            ),
            session_id="s-ok",
            request_id=request_id,
            trace_id=trace_id,
            execution_id=execution_id,
        )
        assert result.snapshot.runtime_profile_version == "1"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_S_SNAP_01_resolve_opens_single_scoped_read() -> None:
    """S-SNAP-01：resolve 走单次 scoped read（配置读在同一一致视图内）。"""
    from fluxion.services.context_resolver import (
        ContextResolver,
        ResolverSelector,
    )
    from tests.runtime_helpers import seed_agent_definition

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    try:
        await _seed_profile(store, "1", default=True)
        await seed_agent_definition(store, provider_id="dev.echo", model_name="dev")
        opened: list[str] = []
        real_begin = store.begin_scoped_read

        def _recording_begin(*, tenant_id: str, timeout_ms: int = 5_000):  # type: ignore[no-untyped-def]
            opened.append(tenant_id)
            return real_begin(tenant_id=tenant_id, timeout_ms=timeout_ms)

        store.begin_scoped_read = _recording_begin  # type: ignore[method-assign]
        resolver = ContextResolver(store)
        request_id = f"req_{'b' * 32}"
        trace_id = f"trace_{'b' * 32}"
        execution_id = f"exec_{'b' * 32}"
        result = await resolver.resolve(
            ResolverSelector(
                tenant_id="tenant-a", agent_id="assistant", user_id="user-a"
            ),
            session_id="s-scope",
            request_id=request_id,
            trace_id=trace_id,
            execution_id=execution_id,
        )
        assert opened == ["tenant-a"]
        assert result.snapshot.runtime_profile_version == "1"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_S_SNAP_01_resolve_across_publish_never_mixed() -> None:
    """S-SNAP-01：publish 前后两次 resolve 各自完整（全旧/全新，无混合）。"""
    from fluxion.services.context_resolver import (
        ContextResolver,
        ResolverSelector,
    )
    from tests.runtime_helpers import seed_agent_definition

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    try:
        await _seed_profile(store, "1", default=True)
        await seed_agent_definition(store, provider_id="dev.echo", model_name="dev")
        resolver = ContextResolver(store)

        def _ids(char: str) -> tuple[str, str, str]:
            return (f"req_{char * 32}", f"trace_{char * 32}", f"exec_{char * 32}")

        request_id, trace_id, execution_id = _ids("c")
        first = await resolver.resolve(
            ResolverSelector(
                tenant_id="tenant-a", agent_id="assistant", user_id="user-a"
            ),
            session_id="s-before",
            request_id=request_id,
            trace_id=trace_id,
            execution_id=execution_id,
        )
        assert first.snapshot.runtime_profile_version == "1"
        # v2 显式携带 default（版本更替后默认不自动延续，治理语义）。
        await _seed_profile(store, "2", default=True)
        request_id2, trace_id2, execution_id2 = _ids("d")
        second = await resolver.resolve(
            ResolverSelector(
                tenant_id="tenant-a", agent_id="assistant", user_id="user-a"
            ),
            session_id="s-after",
            request_id=request_id2,
            trace_id=trace_id2,
            execution_id=execution_id2,
        )
        assert second.snapshot.runtime_profile_version == "2"
        assert (
            first.snapshot.snapshot_digest != second.snapshot.snapshot_digest
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_S_07_resolve_mid_publish_snapshot_all_old(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-07（TASK-009 最终验收）：resolve 中途并发 publish → 快照全旧版，无混合。

    在 agent 读取后、profile 读取前提交 v2（独立连接）；REPEATABLE READ 下
    scope 内后续读仍见 v1。无 scope 接线时 profile 会读到 v2（混合）而失败。
    """
    from fluxion.registry.sqlalchemy_store import _ScopedRegistryReader
    from fluxion.resources import ResourceKind as _Kind
    from fluxion.services.context_resolver import (
        ContextResolver,
        ResolverSelector,
    )
    from tests.runtime_helpers import seed_agent_definition

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    try:
        await _seed_profile(store, "1", default=True)
        await seed_agent_definition(store, provider_id="dev.echo", model_name="dev")
        resolver = ContextResolver(store)
        published: list[str] = []
        real_get = _ScopedRegistryReader.get

        async def _publishing_get(self, kind, resource_id, **kwargs):  # type: ignore[no-untyped-def]
            if kind is _Kind.AGENT_DEFINITION and not published:
                second = PostgreSQLRegistryStore(TEST_POSTGRES_DSN)
                await second.initialize()
                try:
                    await _seed_profile(second, "2", default=True)
                finally:
                    await second.close()
                published.append("2")
            return await real_get(self, kind, resource_id, **kwargs)

        monkeypatch.setattr(_ScopedRegistryReader, "get", _publishing_get)
        request_id = f"req_{'e' * 32}"
        trace_id = f"trace_{'e' * 32}"
        execution_id = f"exec_{'e' * 32}"
        result = await resolver.resolve(
            ResolverSelector(
                tenant_id="tenant-a", agent_id="assistant", user_id="user-a"
            ),
            session_id="s-mid-publish",
            request_id=request_id,
            trace_id=trace_id,
            execution_id=execution_id,
        )
        assert published == ["2"]
        assert result.snapshot.agent_definition_version == "1"
        assert result.snapshot.runtime_profile_version == "1"
        # scope 外已可见 v2：快照是旧视图，不是读不到新数据。
        latest = await store.get(
            _Kind.RUNTIME_PROFILE, "assistant", tenant_id="tenant-a"
        )
        assert latest is not None and latest.version == "2"
    finally:
        await store.close()
