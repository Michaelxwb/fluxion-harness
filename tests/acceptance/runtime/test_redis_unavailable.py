"""E-15: unavailable Redis hints cannot prevent PG queue and result progress."""

import sys
from pathlib import Path

import httpx
import pytest
from muad_agent_runtime.infrastructure.models.async_tools import RunContinuation, ToolResultInbox
from muad_agent_runtime.infrastructure.models.runtime import RunRecord, RuntimeSnapshot
from muad_agent_worker.application.task_service import TaskService
from muad_agent_worker.infrastructure.cancel_hint import create_cancel_hint_store
from muad_agent_worker.infrastructure.models.runtime_operations import RuntimeResultOutbox
from muad_agent_worker.infrastructure.wakeup_hint import create_wakeup_notifier
from muad_agent_worker.results.dispatcher import ResultDispatcher
from muad_agent_worker.worker.service import WorkerLoop
from muad_common import SharedSettings
from muad_contracts import RunStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# `tests/agent_worker/helpers.py` 内部以顶层包名 `agent_worker.*` 导入其 conftest；
# 全量 `tests/acceptance` 跑时靠 im_gateway 用例的 sys.path 副作用才可解析，
# 单跑本文件（登记的 E-15 命令）会在收集期 ModuleNotFoundError。这里显式前置 `tests/`。
_TESTS_ROOT = Path(__file__).resolve().parents[2]
if str(_TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TESTS_ROOT))

from tests.agent_worker.helpers import RecordingExecutor  # noqa: E402  (sys.path 已前置)
from tests.async_tool_helpers import seed_operation  # noqa: E402

pytestmark = pytest.mark.e2e


async def test_e15_pg_progress_with_redis_connection_rejected(live_stack, caplog):
    from tests.acceptance.runtime.conftest import INTERNAL_TOKEN

    engine = create_async_engine(SharedSettings().require_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    import asyncio

    async def redis_fault(reader, writer):
        await reader.read(4096)
        writer.write(b"-ERR injected Redis outage\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    fault_proxy = await asyncio.start_server(redis_fault, "127.0.0.1", 0)
    port = fault_proxy.sockets[0].getsockname()[1]
    notifier = await create_wakeup_notifier(f"redis://127.0.0.1:{port}/0")
    hints = await create_cancel_hint_store(f"redis://127.0.0.1:{port}/0")
    try:
        task, operation, run = await seed_operation(factory, tenant=live_stack.tenant_id)
        async with factory() as session, session.begin():
            snapshot = RuntimeSnapshot(
                tenant_id=task.tenant_id,
                run_id=run.id,
                agent_revision=1,
                model_revision=1,
                agent_json={},
                model_json={},
                skill_catalog_json=[],
                mcp_catalog_json=[],
                policy_json={},
                prompt_template_version="v1",
                content_hash=operation.task_snapshot_hash,
            )
            session.add(snapshot)
            await session.flush()
            waiting = await session.get(RunRecord, run.id)
            waiting.status, waiting.snapshot_id = RunStatus.WAITING_TOOL, snapshot.id
            session.add(
                RunContinuation(
                    tenant_id=task.tenant_id,
                    run_id=run.id,
                    snapshot_id=snapshot.id,
                    context_upto_seq=0,
                    runner_state_json={
                        "schema_version": 1,
                        "phase": "BEFORE_MODEL",
                        "messages": [],
                        "turns": 0,
                        "tool_calls": 0,
                    },
                )
            )
        await notifier.notify()  # Lost wake-up after a durable queue insertion.
        worker = WorkerLoop(factory, executor=RecordingExecutor(), cancel_hints=hints)
        assert await worker.run_once() == task.id
        cancelled, _, _ = await seed_operation(factory, tenant=live_stack.tenant_id)
        async with factory() as session, session.begin():
            status, requested = await TaskService(session).cancel(cancelled.tenant_id, cancelled.id)
            assert status == "CANCELLED" and requested
        await hints.mark(cancelled.id)  # Failed cancellation hint cannot undo PG intent.
        async with httpx.AsyncClient() as client:
            dispatcher = ResultDispatcher(
                factory, client, live_stack.runtime_url, service_token=INTERNAL_TOKEN
            )
            assert await dispatcher.run_once() >= 2
        async with factory() as session:
            inbox = (
                await session.scalars(
                    select(ToolResultInbox).where(ToolResultInbox.operation_id == operation.id)
                )
            ).one()
            assert not inbox.materialized and not inbox.late
            checkpoint = (
                await session.scalars(select(RunContinuation).where(RunContinuation.run_id == run.id))
            ).one()
            assert checkpoint.ready
            assert all(
                row.status.value == "SENT"
                for row in (
                    await session.scalars(
                        select(RuntimeResultOutbox).where(
                            RuntimeResultOutbox.tenant_id.in_([task.tenant_id, cancelled.tenant_id])
                        )
                    )
                )
            )
        # The PG continuation claim must progress without a delivered Redis hint.
        from muad_agent_runtime.application.async_tools.continuation_service import claim_continuation

        restored = await claim_continuation(factory, instance_id="redis-down-restorer", lease_sec=30)
        assert restored is not None and restored.id == run.id
        assert restored.status is RunStatus.RUNNING and restored.execution_epoch == 1
        assert restored.lease_owner == "redis-down-restorer"
        assert "redis_unavailable" in caplog.text
    finally:
        await notifier.aclose()
        await hints.aclose()
        fault_proxy.close()
        await fault_proxy.wait_closed()
        await engine.dispose()
