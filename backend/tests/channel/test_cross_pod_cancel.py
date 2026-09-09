"""STP-03（integration）：多 Pod 取消与 orphan 认领。

- 跨 Pod：svcB 的 /stop 经 PG + Redis 使 svcA 正在执行的任务取消，终态 CANCELLED。
- Redis 丢失：publish 失败不改变取消语义——PG 仍落 CANCELLING，返回 stop_requested。
- Orphan：owner 消失后的超期 CANCELLING 被认领结算为 CANCELLED。

真实边界：双 RuntimeApplicationService 实例 + 真 PG + 真 Redis。
先写测试记 RED：RedisCancelBus、订阅接线、orphan 认领在 TASK-006 实现前不存在。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from fluxion.plugins.contracts import ModelRequest, ModelResponse
from fluxion.plugins.model_provider import ModelProviderRegistry
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ResourceKind
from fluxion.services.runtime_app import RunRuntimeRequest, RuntimeApplicationService
from tests.runtime_helpers import TEST_POSTGRES_DSN, publish_resource, seed_agent_definition


class BlockingProvider:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        self.entered.set()
        await self.release.wait()
        return ModelResponse(provider_id="test", content="done")


def _redis_client():
    import redis.asyncio as redis_asyncio

    return redis_asyncio.from_url("redis://localhost:6379/15", decode_responses=True)


async def _setup_service(
    store: PostgreSQLRegistryStore, blocking: BlockingProvider, **kwargs: object
) -> RuntimeApplicationService:
    providers = ModelProviderRegistry()
    providers.register("test", blocking)
    service = RuntimeApplicationService(store, model_providers=providers, **kwargs)  # type: ignore[arg-type]
    await service.initialize()
    return service


def _setup_uninitialized_service(
    store: PostgreSQLRegistryStore, blocking: BlockingProvider, **kwargs: object
) -> RuntimeApplicationService:
    """构造但不 initialize（同库多实例测试：initialize 会 reset 建库）。

    cancel/status 路径无需 initialize（control 直连 store）；调用方不得 close
    它（engine 归属首个初始化的实例）。
    """
    providers = ModelProviderRegistry()
    providers.register("test", blocking)
    return RuntimeApplicationService(store, model_providers=providers, **kwargs)  # type: ignore[arg-type]


async def _seed(store: PostgreSQLRegistryStore) -> None:
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"max_rounds": 8, "default": True},
    )
    await seed_agent_definition(store, system_prompt="你是测试代理。")


def _run_request(execution_id: str, session_id: str) -> RunRuntimeRequest:
    from uuid import uuid4

    return RunRuntimeRequest(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
        session_id=session_id,
        input_message="long task",
        agent_definition_id="assistant",
        request_id=f"req_{uuid4().hex}",
        trace_id=f"trace_{uuid4().hex}",
        execution_id=execution_id,
    )


def _exec_id() -> str:
    from uuid import uuid4

    return f"exec_{uuid4().hex}"


@pytest.mark.asyncio
async def test_STP03_cross_pod_cancel() -> None:
    from fluxion.services.redis_cancel import RedisCancelBus

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    client_a, client_b = _redis_client(), _redis_client()
    try:
        blocking = BlockingProvider()
        svc_a = await _setup_service(store, blocking, cancel_bus=RedisCancelBus(client_a))
        # B 实例不 initialize（同库 reset 会清掉 A 的执行记录与种子）。
        svc_b = _setup_uninitialized_service(
            store, BlockingProvider(), cancel_bus=RedisCancelBus(client_b)
        )
        try:
            await _seed(store)
            exec_id = _exec_id()
            task = asyncio.ensure_future(svc_a.run(_run_request(exec_id, "sess_xpod_1")))
            try:
                await asyncio.wait_for(blocking.entered.wait(), timeout=10)
                # B 实例（非 owner）发起取消：PG + Redis 双通道。
                result = await svc_b.cancel_active_execution(
                    tenant_id="tenant-a", user_id="user-a",
                    agent_definition_id="assistant", session_id="sess_xpod_1",
                )
                assert result.code == "stop_requested"
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=10)
                record = await store.get_execution(tenant_id="tenant-a", execution_id=exec_id)
                assert record is not None
                assert record.state == "cancelled"
            finally:
                blocking.release.set()
                if not task.done():
                    task.cancel()
                from contextlib import suppress

                with suppress(asyncio.CancelledError, Exception):
                    await task
        finally:
            await svc_a.close()
            # svc_b 未 initialize，不 close（engine 归属 svc_a；重复 close 引擎无意义）。
    finally:
        await client_a.aclose()
        await client_b.aclose()
        await store.close()


@pytest.mark.asyncio
async def test_STP03_redis_loss_keeps_durable_cancel() -> None:
    from fluxion.services.redis_cancel import RedisCancelBus

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    client = _redis_client()
    try:
        blocking = BlockingProvider()
        svc = await _setup_service(store, blocking)
        try:
            await _seed(store)
            exec_id = _exec_id()
            task = asyncio.ensure_future(svc.run(_run_request(exec_id, "sess_nosig_1")))
            try:
                await asyncio.wait_for(blocking.entered.wait(), timeout=10)

                class DeadBus(RedisCancelBus):
                    async def publish_cancel(self, tenant_id: str, execution_id: str) -> None:
                        del tenant_id, execution_id
                        raise ConnectionError("redis down")

                svc_broken = _setup_uninitialized_service(
                    store, BlockingProvider(), cancel_bus=DeadBus(client)
                )
                # svc_broken 未 initialize，不 close（engine 归属 svc）。
                result = await svc_broken.cancel_active_execution(
                    tenant_id="tenant-a", user_id="user-a",
                    agent_definition_id="assistant", session_id="sess_nosig_1",
                )
                # Redis 丢失不改变取消语义：PG 仍落 CANCELLING。
                assert result.code == "stop_requested"
                record = await store.get_execution(tenant_id="tenant-a", execution_id=exec_id)
                assert record is not None
                assert record.state == "cancelling"
            finally:
                blocking.release.set()
                task.cancel()
                from contextlib import suppress

                with suppress(asyncio.CancelledError, Exception):
                    await task
        finally:
            await svc.close()
    finally:
        await client.aclose()
        await store.close()


@pytest.mark.asyncio
async def test_STP03_orphan_cancelling_reconciled() -> None:

    from fluxion.services.execution_control_service import ExecutionControlService

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    try:
        old = datetime.now(UTC) - timedelta(minutes=10)
        owner = ExecutionControlService(
            store, service_instance_id="dead-pod", clock=lambda: old
        )
        exec_id = _exec_id()
        # 构造孤儿：远古时间请求取消（owner 已消失，无人推进）。
        from fluxion.registry import ExecutionRecord

        await store.create_execution(
            ExecutionRecord(
                execution_id=exec_id, tenant_id="tenant-a", platform_user_id="user-a",
                agent_id="assistant", session_id="sess_orphan_1", state="running",
                request_id="req_x", trace_id="trace_x", owner_instance_id="dead-pod",
                requested_skill_id=None, started_at=old, cancel_requested_at=None,
                cancel_reason=None, finished_at=None, error_code=None, revision=0,
            )
        )
        cancelled = await store.request_execution_cancel(
            tenant_id="tenant-a", platform_user_id="user-a", agent_id="assistant",
            session_id="sess_orphan_1", reason="stop_requested", now=old,
        )
        assert cancelled is not None
        del owner

        fresh = ExecutionControlService(store, service_instance_id="new-pod")
        reconciled = await fresh.reconcile_orphans(older_than_seconds=60)
        assert exec_id in reconciled
        record = await store.get_execution(tenant_id="tenant-a", execution_id=exec_id)
        assert record is not None
        assert record.state == "cancelled"
    finally:
        await store.close()
