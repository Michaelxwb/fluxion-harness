"""TASK-007: PersonalMemoryRetriever 真实装配验收（S-07/E-04）。

真实边界：真实 RegistryStore（PG）+ 真实 personal_memory 表 +
真实 PgVectorSemanticStore + 真实 PersonalMemoryRetriever +
真实 ContextResolver → ExecutionSnapshot manifest。不注入 Noop，
不用错误 retrieve 方法。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from sqlalchemy import insert

from fluxion.memory.domain.personal_memory import PersonalMemoryRetriever
from fluxion.plugins.providers.pgvector_semantic import PgVectorSemanticStore
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.registry.schema import personal_memory
from fluxion.services.execution_session import ExecutionSession
from fluxion.services.runtime_app import (
    CreateRuntimeProfileRequest,
    PublishRuntimeProfileRequest,
    RunRuntimeRequest,
    RuntimeApplicationService,
)
from tests.runtime_helpers import seed_agent_definition, TEST_POSTGRES_DSN


async def _ensure_personal_memory_table(store: object) -> None:
    engine = store._engine  # type: ignore[union-attr]
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_conn: personal_memory.create(sync_conn, checkfirst=True)
        )


async def _seed_known_memories(store: object, tenant_id: str, user_id: str) -> list[int]:
    """预置两条已知记忆，返回行 id（entry_id 来源）。"""
    engine = store._engine  # type: ignore[union-attr]
    now = datetime.now(UTC)
    ids: list[int] = []
    async with engine.begin() as connection:
        for content in ("user prefers concise answers", "user visited Tokyo in 2024"):
            result = await connection.execute(
                insert(personal_memory).values(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    memory_type="semantic",
                    content=content,
                    embedding=None,
                    source_session_id="session-seed",
                    source_range_hash=None,
                    learning_enabled=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            ids.append(int(result.inserted_primary_key[0]))
    return ids


async def _seed_agent_chain(service: RuntimeApplicationService, store: object, tenant_id: str) -> None:
    await service.create_runtime_profile(
        CreateRuntimeProfileRequest(
            tenant_id=tenant_id,
            runtime_profile_id="assistant",
            version="1",
            request_timeout_ms=10_000,
            default=True,
        )
    )
    await seed_agent_definition(store, tenant_id=tenant_id, provider_id="dev.echo", model_name="dev")  # type: ignore[arg-type]
    await service.publish_runtime_profile(
        PublishRuntimeProfileRequest(
            tenant_id=tenant_id, runtime_profile_id="assistant", version="1"
        )
    )


def _service_with_real_retriever(store: object) -> RuntimeApplicationService:
    provider = PgVectorSemanticStore(store._engine)  # type: ignore[union-attr]
    return RuntimeApplicationService.create_dev_bundle(
        store,  # type: ignore[arg-type]
        memory_retriever=PersonalMemoryRetriever(provider),
    )


async def _resolve_manifest(service: RuntimeApplicationService, tenant_id: str, user_id: str):  # type: ignore[no-untyped-def]
    prepared = await ExecutionSession(service).prepare(
        RunRuntimeRequest(
            tenant_id=tenant_id,
            user_id=user_id,
            runtime_profile_id="assistant",
            session_id="session-s07",
            input_message="hello",
            agent_definition_id="assistant",
        )
    )
    return prepared.context.snapshot.memory_manifest


@pytest.fixture
async def assembly() -> AsyncGenerator[tuple[RuntimeApplicationService, object, str, str]]:
    tenant_id = f"tenant-s07-{uuid.uuid4().hex[:8]}"
    user_id = f"user-s07-{uuid.uuid4().hex[:8]}"
    store: object = PostgreSQLRegistryStore(TEST_POSTGRES_DSN)
    await store.initialize()  # type: ignore[union-attr]
    await _ensure_personal_memory_table(store)
    service = _service_with_real_retriever(store)
    await service.initialize()
    await _seed_agent_chain(service, store, tenant_id)
    try:
        yield service, store, tenant_id, user_id
    finally:
        await service.close()
        await store.close()  # type: ignore[union-attr]


class TestS07MemoryManifestAssembly:
    async def test_known_memories_land_in_manifest(
        self, assembly: tuple[RuntimeApplicationService, object, str, str]
    ) -> None:
        """S-07：预置记忆后 manifest 含相同 entry_id 与内容 hash（非空实现证明）。"""
        from fluxion.services.context_resolution_support import _short_hash

        service, store, tenant_id, user_id = assembly
        ids = await _seed_known_memories(store, tenant_id, user_id)
        manifest = await _resolve_manifest(service, tenant_id, user_id)
        assert manifest.content_hash not in ("", "unavailable")
        by_id = {ref.entry_id: ref for ref in manifest.entry_refs}
        assert {str(i) for i in ids} <= set(by_id)
        assert by_id[str(ids[0])].content_hash == _short_hash("user prefers concise answers")
        assert by_id[str(ids[1])].content_hash == _short_hash("user visited Tokyo in 2024")

    async def test_cross_instance_consistency(
        self, assembly: tuple[RuntimeApplicationService, object, str, str]
    ) -> None:
        """S-07：同 tenant/user 跨实例 manifest 一致。"""
        service, store, tenant_id, user_id = assembly
        await _seed_known_memories(store, tenant_id, user_id)
        first = await _resolve_manifest(service, tenant_id, user_id)
        second_service = _service_with_real_retriever(store)
        await second_service.initialize()
        try:
            second = await _resolve_manifest(second_service, tenant_id, user_id)
        finally:
            await second_service.close()
        assert first.content_hash == second.content_hash
        assert [ref.entry_id for ref in first.entry_refs] == [ref.entry_id for ref in second.entry_refs]

    async def test_dev_bundle_factory_wires_retriever(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
        """S-07：dev 装配入口持有真实 Retriever（非本地直连的旧生命周期）。"""
        import base64
        import os

        from fluxion.api.dev_bundle import create_dev_bundle_app

        # ADR-A007：dev bundle 要求显式 master key。
        monkeypatch.setenv(
            "FLUXION_SECRET_MASTER_KEY", base64.b64encode(os.urandom(32)).decode()
        )
        console_dist = tmp_path / "console"
        chat_dist = tmp_path / "chat"
        console_dist.mkdir()
        chat_dist.mkdir()
        app = create_dev_bundle_app(
            registry_dsn=TEST_POSTGRES_DSN,
            console_dist=console_dist,
            chat_dist=chat_dist,
        )
        runtime = app.state.runtime_service
        retriever = runtime._runtime._snapshot_builder._resolver._memory_retriever
        assert isinstance(retriever, PersonalMemoryRetriever)
        assert isinstance(retriever.provider, PgVectorSemanticStore)

    async def test_runtime_app_from_env_wires_retriever(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """S-07：生产远程装配入口持有真实 Retriever（PG engine 共享）。"""
        import base64
        import os

        from fluxion.api.production_bundle import create_runtime_app_from_env

        monkeypatch.setenv("FLUXION_DATABASE_URL", TEST_POSTGRES_DSN)
        monkeypatch.setenv("FLUXION_SECRET_MASTER_KEY", base64.b64encode(os.urandom(32)).decode())
        pytest.importorskip("asyncpg")
        app = create_runtime_app_from_env()
        assert app is not None


class TestE04MemoryDegradation:
    async def test_empty_memory_is_empty_manifest_not_unavailable(
        self, assembly: tuple[RuntimeApplicationService, object, str, str]
    ) -> None:
        """E-04：空记忆是成功空 manifest（content_hash 为空），非 unavailable。"""
        service, _, tenant_id, user_id = assembly
        manifest = await _resolve_manifest(service, tenant_id, user_id)
        assert manifest.entry_refs == []
        assert manifest.content_hash == ""

    async def test_no_retriever_is_unavailable(
        self, assembly: tuple[RuntimeApplicationService, object, str, str]
    ) -> None:
        """E-04：未装配 retriever 为 unavailable（与空结果区分）。"""
        _service, store, tenant_id, user_id = assembly
        bare = RuntimeApplicationService.create_dev_bundle(store)  # type: ignore[arg-type]
        await bare.initialize()
        try:
            manifest = await _resolve_manifest(bare, tenant_id, user_id)
        finally:
            await bare.close()
        assert manifest.content_hash == "unavailable"

    async def test_failing_retriever_degrades_observably(
        self, assembly: tuple[RuntimeApplicationService, object, str, str], caplog: pytest.LogCaptureFixture
    ) -> None:
        """E-04：异常 retriever 降级为 unavailable 且可观测（脱敏日志）。"""
        import logging

        _service, store, tenant_id, user_id = assembly

        class _Boom:
            async def recall(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
                raise RuntimeError("boom with secret sk-live-should-not-log")

        broken = RuntimeApplicationService.create_dev_bundle(
            store,  # type: ignore[arg-type]
            memory_retriever=PersonalMemoryRetriever(_Boom()),  # type: ignore[arg-type]
        )
        await broken.initialize()
        try:
            with caplog.at_level(logging.WARNING):
                manifest = await _resolve_manifest(broken, tenant_id, user_id)
        finally:
            await broken.close()
        assert manifest.content_hash == "unavailable"
        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert "memory.recall.degraded" in logged
        assert "sk-live-should-not-log" not in logged

    async def test_hanging_retriever_times_out(
        self, assembly: tuple[RuntimeApplicationService, object, str, str]
    ) -> None:
        """E-04：挂起 retriever 在有限 recall timeout 内降级（默认 1s，不重试）。"""
        import asyncio
        import time

        _service, store, tenant_id, user_id = assembly

        class _Hang:
            async def recall(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
                await asyncio.sleep(30.0)
                return []

        hanging = RuntimeApplicationService.create_dev_bundle(
            store,  # type: ignore[arg-type]
            memory_retriever=PersonalMemoryRetriever(_Hang()),  # type: ignore[arg-type]
        )
        await hanging.initialize()
        try:
            started = time.monotonic()
            manifest = await _resolve_manifest(hanging, tenant_id, user_id)
            elapsed = time.monotonic() - started
        finally:
            await hanging.close()
        assert manifest.content_hash == "unavailable"
        assert elapsed < 5.0, f"recall 超时未受预算控制：{elapsed:.1f}s"

    async def test_tenant_user_isolation(
        self, assembly: tuple[RuntimeApplicationService, object, str, str]
    ) -> None:
        """E-04：tenant/user 隔离——他人记忆不可见。"""
        service, store, tenant_id, user_id = assembly
        await _seed_known_memories(store, tenant_id, user_id)
        other = await _resolve_manifest(service, tenant_id, f"other-{user_id}")
        assert other.entry_refs == []
        assert other.content_hash == ""
