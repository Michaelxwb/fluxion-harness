"""E-03: SIGKILL after Worker terminal commit, independent process replay to Runtime HTTP."""

import asyncio
import os
import signal
import subprocess
import sys

import httpx
import pytest
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent
from muad_agent_worker.infrastructure.models.runtime_operations import RuntimeResultOutbox
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.async_tool_helpers import seed_operation

pytestmark = pytest.mark.e2e

WORKER_COMMIT = """
import asyncio, sys
from pathlib import Path
from uuid import UUID
from muad_agent_worker.infrastructure.db import get_session_factory
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.service import WorkerLoop
async def main():
    factory = get_session_factory()
    async with factory() as session:
        task = await session.get(TaskExecution, UUID(sys.argv[1]))
    worker = WorkerLoop(factory, instance_id='worker-a')
    await worker._handle_success(task, {'value': 'committed'}, now=None)
    Path(sys.argv[2]).write_text('committed')
    await asyncio.Event().wait()
asyncio.run(main())
"""

REPLAY = """
import asyncio, httpx, sys
from muad_agent_worker.infrastructure.db import get_session_factory
from muad_agent_worker.results.dispatcher import ResultDispatcher
async def main():
    async with httpx.AsyncClient() as client:
        dispatcher = ResultDispatcher(get_session_factory(), client, sys.argv[1],
                                      service_token=sys.argv[2], instance_id='worker-b')
        assert await dispatcher.run_once() == 1
asyncio.run(main())
"""


async def _ack_dropping_proxy(runtime_url: str):
    drops = {"remaining": 1}

    async def handle(reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
        headers = dict(line.split(": ", 1) for line in head.split("\r\n")[1:] if ": " in line)
        size = int(next(value for name, value in headers.items() if name.lower() == "content-length"))
        body = await reader.readexactly(size)
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.post(
                runtime_url + "/internal/tool-results",
                content=body,
                headers={name: value for name, value in headers.items() if name.lower() != "host"},
            )
        assert response.status_code == 200
        if drops["remaining"]:
            drops["remaining"] -= 1
            writer.close()  # Commit succeeded, but no HTTP response reaches the dispatcher.
        else:
            content = response.content
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(content)}\r\nConnection: close\r\n\r\n".encode()
                + content
            )
            await writer.drain()
            writer.close()
        await writer.wait_closed()

    proxy = await asyncio.start_server(handle, "127.0.0.1", 0)
    return proxy, f"http://127.0.0.1:{proxy.sockets[0].getsockname()[1]}"


async def test_e03_killed_worker_replay_and_lost_response_exactly_once(live_stack, tmp_path):
    from muad_common import SharedSettings

    from tests.acceptance.runtime.conftest import INTERNAL_TOKEN

    engine = create_async_engine(SharedSettings().require_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    task, operation, _ = await seed_operation(factory, task_status="RUNNING", tenant=live_stack.tenant_id)
    marker = tmp_path / "terminal-committed"
    process = subprocess.Popen(
        [sys.executable, "-c", WORKER_COMMIT, str(task.id), str(marker)], env=os.environ.copy()
    )
    proxy, proxy_url = await _ack_dropping_proxy(live_stack.runtime_url)
    try:
        deadline = asyncio.get_running_loop().time() + 15
        while not marker.exists() and asyncio.get_running_loop().time() < deadline:
            assert process.poll() is None
            await asyncio.sleep(0.05)
        assert marker.exists(), "terminal transaction never committed"
        process.send_signal(signal.SIGKILL)
        await asyncio.to_thread(process.wait, 10)
        async with factory() as session:
            outbox = (
                await session.scalars(
                    select(RuntimeResultOutbox).where(RuntimeResultOutbox.task_id == task.id)
                )
            ).one()
            assert outbox.status.value == "PENDING"
        replay = await asyncio.create_subprocess_exec(sys.executable, "-c", REPLAY, proxy_url, INTERNAL_TOKEN)
        assert await asyncio.wait_for(replay.wait(), 20) == 0
        async with factory() as session:
            assert (await session.get(RuntimeResultOutbox, outbox.id)).status.value == "PENDING"
        await asyncio.sleep(2)  # Injected test wait exceeds the configured first retry's bounded jitter.
        replay = await asyncio.create_subprocess_exec(sys.executable, "-c", REPLAY, proxy_url, INTERNAL_TOKEN)
        assert await asyncio.wait_for(replay.wait(), 20) == 0
        headers = {"X-Internal-Service": INTERNAL_TOKEN, "X-Tenant-Id": task.tenant_id}
        # Replaying the same request models an acknowledgement lost after durable commit.
        async with httpx.AsyncClient() as client:
            response = await client.post(
                live_stack.runtime_url + "/internal/tool-results", json=outbox.payload_json, headers=headers
            )
            assert response.json()["data"]["duplicate"] is True
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(CanonicalEvent)
                    .where(
                        CanonicalEvent.run_id == operation.run_id,
                        CanonicalEvent.event_type == "TOOL_RESULT_RECEIVED",
                    )
                )
                == 1
            )
            assert (await session.get(RuntimeResultOutbox, outbox.id)).status.value == "SENT"
    finally:
        if process.poll() is None:
            process.kill()
            await asyncio.to_thread(process.wait, 10)
        proxy.close()
        await proxy.wait_closed()
        await engine.dispose()
