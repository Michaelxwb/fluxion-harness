"""B-112 / S-06 / E-05: `/stop` 命令（真实 Runtime cancel-active HTTP → PostgreSQL CAS/事件）。

不得 Mock 的真实边界：真实 Runtime 服务进程（真实 socket + 真实 PostgreSQL 行级 CAS 与
canonical_event 写入）+ 真实 Console 服务进程（真实 socket，用于 resolve 身份与授权）；
Gateway 侧驱动生产 `InboundPipeline`。
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

# tests/ 需在 sys.path 上才能复用 console_channel 的真实 Console 夹具（channel/database_guard）
_TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(_TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TESTS_ROOT))

# 以下 import 依赖上面的 sys.path 插入，因此不在文件顶部：逐行标注 E402 例外
import httpx  # noqa: E402
import pytest  # noqa: E402
from console_channel.conftest import (  # noqa: E402, F401  (fixture reuse)
    ChannelContext,
    channel,
    database_guard,
)
from fakes import ConsoleProcess  # noqa: E402
from muad_api.catalog import MessageCatalog  # noqa: E402
from muad_contracts import ChannelEnvelope  # noqa: E402
from muad_im_gateway.application.console_client import ConsoleClient  # noqa: E402
from muad_im_gateway.application.inbound import InboundPipeline  # noqa: E402
from muad_im_gateway.application.runtime_client import RuntimeClient  # noqa: E402
from muad_im_gateway.channels.fake import FakeChannelAdapter  # noqa: E402
from muad_im_gateway.infrastructure.dedupe import NullDedupeStore  # noqa: E402
from sqlalchemy import text  # noqa: E402

from tests.acceptance.task_schedule.environment import (  # noqa: E402
    RUNTIME_CLEANUP,
    ServiceProcess,
    free_port,
    require,
    run_db,
)

STOP_ACCEPTED_TEXT = "正在停止当前任务…"
STOP_CANCELLED_TEXT = "当前任务已停止"
NO_ACTIVE_RUN_TEXT = "当前没有执行中的任务"
NO_PERMISSION_TEXT = "当前账号未获得该智能体使用权限"
RUNTIME_READY_TIMEOUT_SEC = 45.0
INTERNAL_TOKEN = "e2e-stop-internal-token"


def _runtime_env(root: Path, console_url: str) -> dict[str, str]:
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    skill_cache = root / "skill-cache"
    skill_cache.mkdir(parents=True, exist_ok=True)
    from muad_common import SharedSettings

    settings = SharedSettings()
    return {
        **os.environ,
        "DATABASE_URL": require("DATABASE_URL", settings.database_url),
        "REDIS_URL": require("REDIS_URL", settings.redis_url),
        "ARTIFACT_ROOT": str(artifacts),
        "SKILL_CACHE_ROOT": str(skill_cache),
        "CONSOLE_PLATFORM_URL": console_url,
        "INTERNAL_SERVICE_TOKEN": INTERNAL_TOKEN,
    }


@pytest.fixture()
async def runtime_rows(
    channel: ChannelContext,  # noqa: F811  (遮蔽 re-export 的夹具：pytest 按参数名解析)
) -> AsyncIterator[None]:
    """用例结束后清理本租户的 runtime 行（FK 顺序由 RUNTIME_CLEANUP 保证）。"""
    try:
        yield
    finally:
        _purge_runtime_rows(channel)


@pytest.fixture()
async def console_server(tmp_path: Path) -> AsyncIterator[ConsoleProcess]:
    server = ConsoleProcess()
    server.start(tmp_path)
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture()
async def runtime_server(tmp_path: Path, console_server: ConsoleProcess) -> AsyncIterator[ServiceProcess]:
    """真实 Runtime 进程：真实 PG CAS/事件 + 真实 HTTP。"""
    process = ServiceProcess(
        name="runtime",
        module="muad_agent_runtime.main",
        port=free_port(),
        env=_runtime_env(tmp_path, console_server.url),
        log_path=tmp_path / "runtime.log",
    )
    process.start()
    try:
        _wait_runtime_ready(process.url)
        yield process
    finally:
        process.stop()


def _wait_runtime_ready(url: str) -> None:
    deadline = time.monotonic() + RUNTIME_READY_TIMEOUT_SEC
    last = ""
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{url}/healthz", timeout=1.0)
        except httpx.HTTPError as exc:
            last = str(exc)
        else:
            if response.status_code == 200:
                return
            last = response.text
        time.sleep(0.1)
    raise AssertionError(f"runtime 未就绪: {last}")


def _seed_run(
    channel_context: ChannelContext,
    *,
    status: str,
    with_interrupt: bool = False,
    user_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """真实 PG：conversation + run_record（+ 等待中的 interrupt）。"""
    run_id, conversation_id = uuid.uuid4(), uuid.uuid4()
    owner_id = user_id or channel_context.actor_user_id

    async def seed(factory: Any) -> None:
        from muad_agent_runtime.infrastructure.models.runtime import (
            Conversation,
            RunInterrupt,
            RunRecord,
        )

        async with factory() as session:
            session.add(
                Conversation(
                    id=conversation_id,
                    tenant_id=channel_context.tenant_id,
                    user_id=owner_id,
                    agent_id=channel_context.agent_id,
                    status="ACTIVE",
                    last_seq=0,
                )
            )
            session.add(
                RunRecord(
                    id=run_id,
                    tenant_id=channel_context.tenant_id,
                    conversation_id=conversation_id,
                    user_id=owner_id,
                    agent_id=channel_context.agent_id,
                    status=status,
                    input_text="pending",
                    trace_id=uuid.uuid4().hex,
                    cancel_requested=False,
                )
            )
            if with_interrupt:
                session.add(
                    RunInterrupt(
                        tenant_id=channel_context.tenant_id,
                        run_id=run_id,
                        conversation_id=conversation_id,
                        interrupt_type="CONFIRM",
                        prompt_text="continue?",
                        options_json=["yes", "no"],
                        status="WAITING",
                    )
                )
            await session.commit()

    run_db(seed)
    return run_id, conversation_id


def _purge_runtime_rows(channel_context: ChannelContext) -> None:
    async def purge(factory: Any) -> None:
        async with factory() as session:
            async with session.begin():
                for statement in RUNTIME_CLEANUP:
                    await session.execute(text(statement), {"t": channel_context.tenant_id})

    run_db(purge)


async def _run_row(run_id: uuid.UUID) -> dict[str, Any]:
    from muad_agent_runtime.infrastructure.db import get_session_factory

    async with get_session_factory()() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, cancel_requested FROM runtime.run_record WHERE id = :id"
                ),
                {"id": run_id},
            )
        ).one()
    return {"status": row[0], "cancel_requested": row[1]}


async def _interrupt_row(run_id: uuid.UUID) -> dict[str, Any]:
    from muad_agent_runtime.infrastructure.db import get_session_factory

    async with get_session_factory()() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, resolution_json FROM runtime.run_interrupt WHERE run_id = :id"
                ),
                {"id": run_id},
            )
        ).one()
    return {"status": row[0], "resolution": row[1]}


async def _canonical_events(run_id: uuid.UUID) -> list[dict[str, Any]]:
    from muad_agent_runtime.infrastructure.db import get_session_factory

    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT event_type, stream_type, payload_json FROM runtime.canonical_event "
                    "WHERE run_id = :id ORDER BY seq"
                ),
                {"id": run_id},
            )
        ).all()
    return [{"event_type": row[0], "stream_type": row[1], "payload": row[2]} for row in rows]


def _envelope(
    text: str,
    *,
    message_id: str,
    channel_context: ChannelContext,
    external_user_id: str | None = None,
) -> ChannelEnvelope:
    return ChannelEnvelope(
        channel="WECOM",
        bot_id=channel_context.bot_id,
        external_user_id=external_user_id or channel_context.bound_external_user_id,
        external_conversation_id="conv-stop",
        message_id=message_id,
        text=text,
    )


def _build_stack(
    console_url: str,
    runtime_url: str,
    catalog: MessageCatalog,
    tenant_id: str,
) -> tuple[FakeChannelAdapter, InboundPipeline, ConsoleClient, RuntimeClient]:
    adapter = FakeChannelAdapter()
    console = ConsoleClient(console_url)
    runtime = RuntimeClient(runtime_url)
    pipeline = InboundPipeline(
        dedupe=NullDedupeStore(),
        console=console,
        runtime=runtime,
        catalog=catalog,
        tenant_id=tenant_id,
    )
    return adapter, pipeline, console, runtime


async def _stop(
    adapter: FakeChannelAdapter,
    pipeline: InboundPipeline,
    console: ConsoleClient,
    runtime: RuntimeClient,
    channel_context: ChannelContext,
    *,
    external_user_id: str | None = None,
    message_id: str | None = None,
) -> list[str]:
    try:
        await pipeline.handle(
            adapter,
            _envelope(
                "/stop",
                message_id=message_id or f"msg-stop-{uuid.uuid4().hex[:8]}",
                channel_context=channel_context,
                external_user_id=external_user_id,
            ),
        )
    finally:
        await console.aclose()
        await runtime.aclose()
    return [message.text for _route, message in adapter.sent]


async def test_s06_stop_on_waiting_input_cancels_immediately_with_readable_events(
    console_server: ConsoleProcess,
    runtime_server: ServiceProcess,
    catalog: MessageCatalog,
    channel: ChannelContext,  # noqa: F811  (遮蔽 re-export 的夹具：pytest 按参数名解析)
    runtime_rows: None,
) -> None:
    run_id, _conversation_id = _seed_run(channel, status="WAITING_INPUT", with_interrupt=True)
    adapter, pipeline, console, runtime = _build_stack(
        console_server.url, runtime_server.url, catalog, channel.tenant_id
    )
    replies = await _stop(adapter, pipeline, console, runtime, channel)

    # WAITING_INPUT 已被 Runtime 直接 CAS 为 CANCELLED：立即"已停止"，不是"正在停止"；
    # cancel_requested 如实记录"这次取消被请求过"（TASK-003 的 `_cancel_waiting` 口径）。
    assert replies == [STOP_CANCELLED_TEXT]
    assert await _run_row(run_id) == {"status": "CANCELLED", "cancel_requested": True}
    interrupt = await _interrupt_row(run_id)
    assert interrupt["status"] == "CANCELLED"
    assert interrupt["resolution"] == {"reason": "cancelled"}
    events = await _canonical_events(run_id)
    # 取消事件可回读：CANCEL + RUN_COMPLETED（流名 run.completed，终态 CANCELLED）
    assert [event["stream_type"] for event in events if event["stream_type"]] == ["run.completed"]
    assert any(
        event["event_type"] == "RUN_COMPLETED"
        and (event["payload"] or {}).get("status") == "CANCELLED"
        for event in events
    )


async def test_b112_stop_on_running_run_is_accepted_not_reported_completed(
    console_server: ConsoleProcess,
    runtime_server: ServiceProcess,
    catalog: MessageCatalog,
    channel: ChannelContext,  # noqa: F811  (遮蔽 re-export 的夹具：pytest 按参数名解析)
    runtime_rows: None,
) -> None:
    run_id, _conversation_id = _seed_run(channel, status="RUNNING")
    adapter, pipeline, console, runtime = _build_stack(
        console_server.url, runtime_server.url, catalog, channel.tenant_id
    )
    replies = await _stop(adapter, pipeline, console, runtime, channel)

    # CREATED/RUNNING 只受理：文案是"正在停止"，且不冒充已完成
    assert replies == [STOP_ACCEPTED_TEXT]
    row = await _run_row(run_id)
    assert row == {"status": "RUNNING", "cancel_requested": True}
    assert await _canonical_events(run_id) == []


async def test_e05_stop_without_active_run_reports_no_active_task(
    console_server: ConsoleProcess,
    runtime_server: ServiceProcess,
    catalog: MessageCatalog,
    channel: ChannelContext,  # noqa: F811  (遮蔽 re-export 的夹具：pytest 按参数名解析)
    runtime_rows: None,
) -> None:
    adapter, pipeline, console, runtime = _build_stack(
        console_server.url, runtime_server.url, catalog, channel.tenant_id
    )
    # 真实 Runtime HTTP：无活跃 Run → NO_ACTIVE_RUN（catalog 映射为 404）
    async with httpx.AsyncClient(base_url=runtime_server.url, timeout=5.0) as client:
        raw = await client.post(
            "/v1/runs/cancel-active",
            json={"agent_id": str(channel.agent_id), "platform_user_id": str(channel.actor_user_id)},
            headers={"X-Tenant-Id": channel.tenant_id},
        )
    assert raw.status_code == 404
    assert raw.json()["code"] == "NO_ACTIVE_RUN"

    replies = await _stop(adapter, pipeline, console, runtime, channel)

    assert replies == [NO_ACTIVE_RUN_TEXT]


async def test_b112_stop_without_permission_does_not_cancel(
    console_server: ConsoleProcess,
    runtime_server: ServiceProcess,
    catalog: MessageCatalog,
    channel: ChannelContext,  # noqa: F811  (遮蔽 re-export 的夹具：pytest 按参数名解析)
    runtime_rows: None,
) -> None:
    # 该 Run 属于"已绑定但无 Agent 授权"的用户：未授权 /stop 绝不允许取消它
    run_id, _conversation_id = _seed_run(
        channel, status="RUNNING", user_id=channel.ungranted_user_id
    )
    adapter, pipeline, console, runtime = _build_stack(
        console_server.url, runtime_server.url, catalog, channel.tenant_id
    )
    replies = await _stop(
        adapter,
        pipeline,
        console,
        runtime,
        channel,
        external_user_id=channel.ungranted_external_user_id,
    )

    # 权限拒绝不得发送取消受理/已停止文案，也不得触碰 Run
    assert replies == [NO_PERMISSION_TEXT]
    assert await _run_row(run_id) == {"status": "RUNNING", "cancel_requested": False}
