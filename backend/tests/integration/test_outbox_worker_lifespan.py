from __future__ import annotations

import asyncio

from sqlalchemy import select

from fluxion.registry import (
    BindingCommand,
    BindingOperation,
    SQLiteRegistryStore,
)
from fluxion.registry.schema import outbox_events
from fluxion.resources import ResourceBinding, ResourceKind, SubjectType
from fluxion.runtime.hot_reload import ConfigChangeEvent
from fluxion.services.outbox import InProcessConfigEventPublisher, OutboxWorker


async def test_S_A7_outbox_worker_drains_binding_event_and_maps_kind() -> None:
    """A7：OutboxWorker 接线后 drain PENDING→PUBLISHED，并对 A12 的 binding
    outbox 行（aggregate_type="binding"）正确映射 ConfigChangeEvent——此前
    _config_event 做 ResourceKind("binding") 必崩。publisher 收到 kind=MCP、
    resource_id=github 的事件，push-invalidation 路径打通。"""
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        await store.commit_binding(
            BindingCommand(
                event_id="evt-A7",
                tenant_id="tenant-a",
                binding_id="bind-A7",
                operation=BindingOperation.CREATE,
                actor_id="admin-a",
                request_id="req-A7",
                trace_id="trace-A7",
                binding=ResourceBinding(
                    binding_id="bind-A7",
                    tenant_id="tenant-a",
                    subject_type=SubjectType.USER,
                    subject_id="user-a",
                    resource_type=ResourceKind.MCP,
                    resource_id="github",
                    resource_version_selector="latest-published",
                    config_json={"enabled_tools": ["list_pr"]},
                    credential_ref=None,
                    enabled=True,
                ),
            )
        )
        received: list[ConfigChangeEvent] = []
        worker = OutboxWorker(
            store,
            InProcessConfigEventPublisher(received.append),
            worker_id="test-A7",
        )
        worker.start(interval_seconds=0.01)
        try:
            drained = False
            for _ in range(100):
                async with store.engine.connect() as connection:
                    row = (
                        await connection.execute(
                            select(outbox_events).where(
                                outbox_events.c.event_id == "evt-A7"
                            )
                        )
                    ).mappings().first()
                if row is not None and row["status"] == "published":
                    drained = True
                    break
                await asyncio.sleep(0.01)
        finally:
            await worker.stop()

        assert drained, "outbox row never drained to published"
        assert len(received) == 1
        event = received[0]
        # _config_event 对 binding 用 payload.resource_type → 合法 ResourceKind，不崩
        assert event.kind is ResourceKind.MCP
        assert event.resource_id == "github"
        assert event.tenant_id == "tenant-a"
        assert event.revision >= 1
    finally:
        await store.close()


async def test_S_A7_outbox_worker_start_is_idempotent_and_stop_cancels() -> None:
    """A7：start() 幂等（重复 start 不起第二个 task），stop() 清理 task。"""
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        worker = OutboxWorker(
            store,
            InProcessConfigEventPublisher(lambda _event: None),
            worker_id="test-A7-idem",
        )
        assert worker._task is None
        worker.start(interval_seconds=1.0)
        first_task = worker._task
        assert first_task is not None
        worker.start(interval_seconds=1.0)  # 幂等：不起第二个
        assert worker._task is first_task
        await worker.stop()
        assert worker._task is None
        assert first_task.cancelled() or first_task.done()
    finally:
        await store.close()
