"""S-20：Gateway SSE 建连/重连携带服务身份，Runtime 只在建连时校验一次。

不得 Mock 的真实边界：
- Gateway 使用真实 `RuntimeClient`（`open_events` / `open_event_stream`），只把传输替换成
  真实 Runtime FastAPI 的 ASGI transport；
- Runtime 的门控是真实 `require_internal_service`，事件来自真实 PG 的 canonical 行；
- 断言真实 HTTP 请求数：一次建连/一次重连各只产生一个请求，事件数量不改变请求数。

关键断言（backend design §3.4/§3.5；NFR-SEC-03/NFR-PERF-03）：
- 首次建连带服务身份；重连带服务身份；
- 无/错误身份被拒绝（SSE 建连也返回 403，不开始流）；
- 不逐事件校验、不增加网络往返（N 个事件仍只有 1 个出站请求）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    RunRecord,
)
from muad_agent_runtime.main import app as runtime_app
from muad_api.security import INTERNAL_SERVICE_HEADER
from muad_common import process_settings
from muad_contracts import RunStatus
from muad_im_gateway.application.runtime_client import RuntimeClient
from sqlalchemy import delete

pytestmark = pytest.mark.integration

TOKEN = "s20-runtime-internal-token"
FORBIDDEN = "FORBIDDEN"


class RecordingTransport(httpx.AsyncBaseTransport):
    """真实 ASGI 传输的观测外壳：只记录出站请求，不改变请求/响应。"""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


@dataclass(frozen=True)
class SeededRun:
    tenant_id: str
    run_id: uuid.UUID
    conversation_id: uuid.UUID


@pytest.fixture(autouse=True)
def runtime_identity(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """模拟 Runtime 启动：环境 Token 冻结到 `app.state.settings`（启动缓存）。"""
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)
    runtime_app.state.settings = process_settings()
    runtime_app.state.instance_id = "s20-instance"
    runtime_app.state.execution_supervisor = ExecutionSupervisor()
    try:
        yield
    finally:
        runtime_app.state.settings = None
        runtime_app.state.execution_supervisor = None


async def _seed_terminal_run() -> SeededRun:
    """真实 PG 种一条终态 Run + 两条 canonical 行（非终态 + 终态）。"""
    seeded = SeededRun(
        tenant_id=f"s20-{uuid.uuid4().hex[:10]}",
        run_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
    )
    user_id, agent_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    async with get_session_factory()() as session, session.begin():
        session.add(
            Conversation(
                id=seeded.conversation_id,
                tenant_id=seeded.tenant_id,
                user_id=user_id,
                agent_id=agent_id,
                status="ACTIVE",
                last_seq=2,
            )
        )
        await session.flush()
        session.add(
            RunRecord(
                id=seeded.run_id,
                tenant_id=seeded.tenant_id,
                conversation_id=seeded.conversation_id,
                user_id=user_id,
                agent_id=agent_id,
                status=RunStatus.COMPLETED,
                input_text="s20 stream probe",
                trace_id=f"s20-{uuid.uuid4().hex[:12]}",
                start_time=now,
                end_time=now,
            )
        )
        await session.flush()
        session.add_all(
            [
                CanonicalEvent(
                    tenant_id=seeded.tenant_id,
                    conversation_id=seeded.conversation_id,
                    run_id=seeded.run_id,
                    seq=1,
                    event_type="RUN_STAGE",
                    stream_type="run.stage",
                    payload_json={"status": "RUNNING"},
                ),
                CanonicalEvent(
                    tenant_id=seeded.tenant_id,
                    conversation_id=seeded.conversation_id,
                    run_id=seeded.run_id,
                    seq=2,
                    event_type="RUN_COMPLETED",
                    stream_type="run.completed",
                    payload_json={"status": "COMPLETED", "final_text": "ok"},
                ),
            ]
        )
    return seeded


@pytest.fixture
async def seeded_run() -> AsyncIterator[SeededRun]:
    seeded = await _seed_terminal_run()
    try:
        yield seeded
    finally:
        async with get_session_factory()() as session, session.begin():
            await session.execute(
                delete(CanonicalEvent).where(CanonicalEvent.tenant_id == seeded.tenant_id)
            )
            await session.execute(
                delete(RunRecord).where(RunRecord.tenant_id == seeded.tenant_id)
            )
            await session.execute(
                delete(Conversation).where(Conversation.tenant_id == seeded.tenant_id)
            )


async def _read(client: RuntimeClient, seeded: SeededRun, after_seq: int) -> list[str]:
    async with client.open_event_stream(
        seeded.run_id, tenant_id=seeded.tenant_id, after_seq=after_seq
    ) as events:
        return [event.type async for event in events]


async def test_s20_first_connect_and_reconnect_carry_service_identity(
    seeded_run: SeededRun,
) -> None:
    """[S-20] 首次建连与重连都带身份；重连只按游标续跟，不重复执行。"""
    transport = RecordingTransport(ASGITransport(app=runtime_app))
    client = RuntimeClient("http://runtime.test", transport=transport, service_token=TOKEN)
    try:
        first = await _read(client, seeded_run, after_seq=0)
        assert first == ["run.stage", "run.completed"], first
        assert len(transport.requests) == 1
        assert transport.requests[0].headers[INTERNAL_SERVICE_HEADER] == TOKEN

        reconnected = await _read(client, seeded_run, after_seq=1)
        assert reconnected == ["run.completed"]
        assert len(transport.requests) == 2
        assert transport.requests[1].headers[INTERNAL_SERVICE_HEADER] == TOKEN
        assert transport.requests[1].url.params["after_seq"] == "1"
    finally:
        await client.aclose()


async def test_s20_open_events_reconnect_carries_service_identity(
    seeded_run: SeededRun,
) -> None:
    """[S-20] 断流重连走 `open_events` 时同样每次连接都带身份。"""
    transport = RecordingTransport(ASGITransport(app=runtime_app))
    client = RuntimeClient("http://runtime.test", transport=transport, service_token=TOKEN)
    try:
        first = [event.type async for event in client.open_events(
            str(seeded_run.run_id), tenant_id=seeded_run.tenant_id, after_seq=0
        )]
        assert first == ["run.stage", "run.completed"], first
        reconnected = [event.type async for event in client.open_events(
            str(seeded_run.run_id), tenant_id=seeded_run.tenant_id, after_seq=1
        )]
        assert reconnected == ["run.completed"]
    finally:
        await client.aclose()

    assert len(transport.requests) == 2
    assert [request.headers[INTERNAL_SERVICE_HEADER] for request in transport.requests] == [
        TOKEN,
        TOKEN,
    ]


async def test_s20_sse_endpoint_rejects_missing_and_wrong_identity(
    seeded_run: SeededRun,
) -> None:
    """[S-20] SSE 建连前校验身份：无/错 header 直接 403，不开始事件流。"""
    path = f"/v1/runs/{seeded_run.run_id}/events?after_seq=0"
    transport = ASGITransport(app=runtime_app)
    async with AsyncClient(transport=transport, base_url="http://runtime.test") as client:
        for headers in (
            {"X-Tenant-Id": seeded_run.tenant_id},
            {"X-Tenant-Id": seeded_run.tenant_id, INTERNAL_SERVICE_HEADER: "browser-forged"},
        ):
            denied = await client.get(path, headers=headers)
            assert denied.status_code == 403, denied.text
            assert denied.json()["code"] == FORBIDDEN
            assert "run.stage" not in denied.text

        accepted = await client.get(
            path,
            headers={
                "X-Tenant-Id": seeded_run.tenant_id,
                INTERNAL_SERVICE_HEADER: TOKEN,
            },
        )
        assert accepted.status_code == 200, accepted.text
        assert "event: run.stage" in accepted.text
        assert "event: run.completed" in accepted.text


async def test_s20_identity_is_checked_once_per_connection_not_per_event(
    seeded_run: SeededRun,
) -> None:
    """[S-20] 两个事件只对应一个出站请求：不逐事件校验、不增加网络往返。"""
    transport = RecordingTransport(ASGITransport(app=runtime_app))
    client = RuntimeClient("http://runtime.test", transport=transport, service_token=TOKEN)
    try:
        async with client.open_event_stream(
            seeded_run.run_id, tenant_id=seeded_run.tenant_id, after_seq=0
        ) as events:
            seen = [event.type async for event in events]
    finally:
        await client.aclose()

    assert seen == ["run.stage", "run.completed"], seen
    assert len(transport.requests) == 1, [request.url for request in transport.requests]
    assert transport.requests[0].headers[INTERNAL_SERVICE_HEADER] == TOKEN
